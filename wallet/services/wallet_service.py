import logging
import random
import time
from decimal import Decimal
from functools import wraps
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.db.transaction import TransactionManagementError
from django.utils import timezone

from wallet.models import Wallet, WalletFundingRequest, WalletPayment, WalletTransaction

from . import zainpay_service
from .exceptions import InsufficientFundsError, WalletInactiveError
from .payment_service import create_payment_record

logger = logging.getLogger(__name__)

MAX_LOCK_RETRIES = 5


def _retry_on_lock(func):
    """
    SQLite serializes all writes at the database-file level. Under enough
    concurrent write pressure a writer can still receive "database is locked"
    instead of queueing for the lock (observed in practice on this stack, even
    with a generous busy_timeout configured). A lock error can also leave the
    current connection's transaction state broken for the next query, so on
    retry we discard the connection entirely and let Django open a fresh one.
    Retrying the whole operation a handful of times with a short randomized
    backoff is the standard, simple fix for this rather than introducing a
    different database engine.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        attempt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except OperationalError as e:
                attempt += 1
                if 'locked' not in str(e).lower() or attempt >= MAX_LOCK_RETRIES:
                    raise
                connection.close()
                time.sleep(random.uniform(0.05, 0.2) * attempt)
            except TransactionManagementError:
                # Only reachable after a prior lock error left this connection's
                # transaction state broken - discard it and retry from scratch.
                attempt += 1
                if attempt >= MAX_LOCK_RETRIES:
                    raise
                connection.close()
                time.sleep(random.uniform(0.05, 0.2) * attempt)
    return wrapper


@_retry_on_lock
def credit_wallet(wallet_id, amount, reference, narration, source, metadata=None):
    """
    Atomically credits a wallet and writes the immutable ledger entry.
    Idempotent on `reference` - calling this twice with the same reference
    returns the original transaction without crediting twice.
    """
    amount = Decimal(str(amount))

    existing = WalletTransaction.objects.filter(reference=reference).first()
    if existing:
        return existing

    with transaction.atomic():
        # select_for_update() is a no-op on SQLite (no per-row locking support),
        # but is kept here as real defense-in-depth if this ever moves to a
        # backend that supports it. The actual race-safety on SQLite comes from
        # the single-statement F() update below, which the database evaluates
        # atomically regardless of row-level locking.
        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        if not wallet.is_active:
            raise WalletInactiveError('This wallet is not active.')

        Wallet.objects.filter(id=wallet_id).update(
            balance=F('balance') + amount, updated_at=timezone.now()
        )
        wallet.refresh_from_db(fields=['balance'])
        balance_after = wallet.balance
        balance_before = balance_after - amount

        try:
            txn = WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type=WalletTransaction.CREDIT,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
                reference=reference,
                narration=narration,
                source=source,
                metadata=metadata or {},
            )
        except IntegrityError:
            # Another concurrent call with the same reference won the race and
            # already wrote the ledger entry - undo our balance increment and
            # return the transaction that actually got recorded.
            Wallet.objects.filter(id=wallet_id).update(
                balance=F('balance') - amount, updated_at=timezone.now()
            )
            return WalletTransaction.objects.get(reference=reference)

    return txn


@_retry_on_lock
def debit_wallet(wallet_id, amount, reference, narration, source, metadata=None):
    """
    Atomically debits a wallet and writes the immutable ledger entry.
    Raises InsufficientFundsError if the wallet cannot cover the amount.
    Idempotent on `reference`, same as credit_wallet().
    """
    amount = Decimal(str(amount))

    existing = WalletTransaction.objects.filter(reference=reference).first()
    if existing:
        return existing

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        if not wallet.is_active:
            raise WalletInactiveError('This wallet is not active.')

        # Conditional update: the balance__gte=amount check and the decrement
        # are evaluated by the database as a single atomic statement, so two
        # concurrent debits against the same wallet can never both succeed
        # past the available balance even without row-level locking.
        updated = Wallet.objects.filter(id=wallet_id, balance__gte=amount).update(
            balance=F('balance') - amount, updated_at=timezone.now()
        )
        if updated == 0:
            raise InsufficientFundsError(
                f'Insufficient balance to debit ₦{amount} from this wallet.'
            )

        wallet.refresh_from_db(fields=['balance'])
        balance_after = wallet.balance
        balance_before = balance_after + amount

        try:
            txn = WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type=WalletTransaction.DEBIT,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
                reference=reference,
                narration=narration,
                source=source,
                metadata=metadata or {},
            )
        except IntegrityError:
            # True duplicate-reference race: undo the decrement we just applied
            # and return the transaction that already exists.
            Wallet.objects.filter(id=wallet_id).update(
                balance=F('balance') + amount, updated_at=timezone.now()
            )
            return WalletTransaction.objects.get(reference=reference)

    return txn


def pay_school_fees_from_wallet(parent_account, fee_selections, session, term):
    """
    Pays fees for one or more linked students from the parent's wallet.
    fee_selections is a list of {'student', 'amount', 'fee_structure'} dicts
    for school fees, or {'student', 'amount', 'other_fee'} dicts for other
    fees (cardigan, tahfeez, etc) - the two kinds are never mixed in one
    payment, matching the existing Paystack flow's separate School Fees /
    Other Fees tabs. The caller must have already validated that each
    student belongs to this parent and that each amount is real.

    When settings.WALLET_FUNDING_PROVIDER == 'zainpay' (the current default)
    AND settings.ZAINPAY_LIVE_TRANSFER_ENABLED is True, wallet funds are
    pooled in a single Zainpay Zainbox rather than sitting in the school's
    own settlement account, so paying fees is a real money movement: a
    Zainbox-to-Zainbox transfer to the school's Zainbox *before* writing
    anything to our ledger. Only on a confirmed transfer do we debit the
    wallet - if it fails, nothing is written, so the ledger never disagrees
    with what Zainpay actually did.

    ZAINPAY_LIVE_TRANSFER_ENABLED=False pauses that real transfer without
    touching wallet funding at all - fees are then just internal ledger
    bookkeeping, same as when the provider is 'paystack' (wallet funds
    already sit in the school's own Paystack settlement account, so no
    external call is needed there either).
    """
    # Fetch fresh rather than trust parent_account.wallet - Django caches that
    # reverse OneToOne lookup on the instance, so a caller that accessed it
    # earlier (e.g. before a deposit landed) would otherwise see a stale balance.
    wallet = Wallet.objects.get(parent_account=parent_account)

    if not wallet.is_active:
        raise WalletInactiveError('This wallet is not active.')

    total_fee_amount = sum(Decimal(str(item['amount'])) for item in fee_selections)
    if total_fee_amount <= 0:
        raise ValueError('Nothing to pay.')

    txn_ref = str(uuid4())
    narration = f"Fee payment - {session} {term}"
    transfer_data = None

    if settings.WALLET_FUNDING_PROVIDER == 'zainpay' and settings.ZAINPAY_LIVE_TRANSFER_ENABLED:
        # Fail fast before calling Zainpay at all if the wallet clearly can't
        # cover the fee plus the estimated transfer charge. This is just a UX
        # short-circuit - the real, authoritative check is debit_wallet()'s
        # atomic conditional update below, using Zainpay's actual amount charged.
        if wallet.balance < total_fee_amount + settings.ZAINPAY_TRANSFER_FEE_ESTIMATE:
            raise InsufficientFundsError('Insufficient wallet balance for this payment.')

        transfer_data = zainpay_service.transfer_wallet_to_school(total_fee_amount, txn_ref, narration)

        # Zainpay's transfer response returns totalTxnAmount in KOBO (same
        # direction as the request). Divide by 100 to get naira for our ledger.
        # Fall back to total_fee_amount (naira) only if the field is absent.
        raw_kobo = transfer_data.get('totalTxnAmount')
        actual_debit_amount = Decimal(str(raw_kobo)) / 100 if raw_kobo is not None else total_fee_amount
    else:
        if wallet.balance < total_fee_amount:
            raise InsufficientFundsError('Insufficient wallet balance for this payment.')
        actual_debit_amount = total_fee_amount

    try:
        with transaction.atomic():
            wallet_txn = debit_wallet(
                wallet.id,
                actual_debit_amount,
                txn_ref,
                f"Wallet payment for fees ({session} {term})",
                source='wallet_fee_payment',
                metadata=transfer_data,
            )

            payments = []
            for index, item in enumerate(fee_selections):
                # Indexed rather than keyed on student id alone, since other
                # fees can include several items for the same student (e.g.
                # cardigan + tahfeez), which would otherwise collide.
                payment = create_payment_record(
                    student=item['student'],
                    amount=item['amount'],
                    payment_method='wallet',
                    session=session,
                    term=term,
                    transaction_reference=f"{txn_ref}-{index}",
                    fee_structure=item.get('fee_structure'),
                    other_fee=item.get('other_fee'),
                )
                payments.append(payment)

            wallet_payment = WalletPayment.objects.create(
                wallet_transaction=wallet_txn, session=session, term=term,
            )
            wallet_payment.payments.set(payments)
    except Exception:
        if transfer_data is not None:
            # The real Zainpay transfer already succeeded by this point - the
            # money has left the wallet Zainbox regardless of what happens to
            # our own bookkeeping. This must never fail silently: it needs a
            # human to reconcile manually, since Zainpay transfers can't be
            # reversed through this integration.
            logger.critical(
                'Zainpay transfer %s for wallet %s succeeded but recording it failed locally. '
                'Manual reconciliation required. Transfer data: %s',
                txn_ref, wallet.id, transfer_data,
            )
        raise

    return wallet_payment


def credit_zainpay_deposit_if_found(reference):
    """
    Looks up one specific deposit by reference in the wallet Zainbox's
    transaction history and credits the matching wallet if found. Used by
    wallet_fund_callback for immediate, on-redirect crediting instead of
    waiting for the deposit.success webhook (which isn't reliably arriving)
    or the periodic sync_zainpay_transactions reconciliation pass.

    Returns True if the deposit is now credited (or was already credited
    earlier by the webhook/reconciliation command - idempotent either way),
    False if Zainpay doesn't have a matching deposit yet (e.g. the parent
    hasn't actually completed the bank transfer, or it hasn't settled yet).
    """
    if WalletTransaction.objects.filter(reference=reference).exists():
        return True

    funding_request = WalletFundingRequest.objects.filter(reference=reference).first()
    if not funding_request:
        return False

    transactions = zainpay_service.list_account_transactions(settings.ZAINPAY_WALLET_ACCOUNT_NUMBER)
    matching = next(
        (t for t in transactions if t.get('transactionRef') == reference and t.get('transactionType') == 'deposit'),
        None,
    )
    if not matching:
        return False

    amount = Decimal(str(matching.get('amount', 0))) / 100
    credit_wallet(
        wallet_id=funding_request.wallet_id,
        amount=amount,
        reference=reference,
        narration='Wallet funding via Zainpay',
        source='zainpay_callback_verified',
        metadata=matching,
    )
    return True
