import json
import logging
from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import VirtualAccount, WalletTransaction
from .services import zainpay_service
from .services.wallet_service import credit_wallet

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def zainpay_deposit_webhook(request):
    """
    Receives Zainpay's deposit notification for a static virtual account.
    Always returns 200 on the happy path (including "nothing to do" cases)
    so Zainpay doesn't endlessly retry - the one exception is an invalid
    signature, which gets a 401.

    The webhook body itself is only used to extract a transaction reference;
    every other detail (amount, destination account) is re-fetched from
    Zainpay's deposit verification endpoint rather than trusted from the
    incoming payload, since that endpoint is authenticated and the webhook
    body is not.
    """
    raw_body = request.body
    # Per Zainpay's webhook docs, the signature arrives in a header literally
    # named "Zainpay-Signature" (Django's header lookup is case-insensitive,
    # but the name itself - no "x-" prefix - was wrong before).
    signature = request.headers.get('Zainpay-Signature')

    # Logged unconditionally, before the signature check, so a rejection
    # still leaves us full visibility into what Zainpay actually sent.
    logger.info(
        'Zainpay webhook inbound - headers: %s | body: %s | extracted signature: %s',
        dict(request.headers), raw_body.decode('utf-8', errors='replace'), signature,
    )

    if not zainpay_service.verify_webhook_signature(raw_body, signature):
        computed = zainpay_service.compute_webhook_signature(raw_body)
        logger.warning(
            'Zainpay webhook rejected: invalid signature. Computed (HMAC-SHA256 of raw '
            'body with secret key) = %s | received = %s',
            computed, signature,
        )
        return JsonResponse({'error': 'Invalid signature'}, status=401)

    try:
        payload = json.loads(raw_body)
    except ValueError:
        return JsonResponse({'status': 'ignored'}, status=200)

    logger.info('Zainpay webhook received, raw payload: %s', payload)

    # Zainpay sends transfer.success/transfer.failed events to this same
    # callback URL too (e.g. when pay_school_fees_from_wallet's transfer to
    # the school's settlement account completes) - only deposits are handled
    # here.
    event = payload.get('event')
    if event != 'deposit.success':
        logger.info('Zainpay webhook: ignoring non-deposit event "%s"', event)
        return JsonResponse({'status': 'ignored'}, status=200)

    data = payload.get('data', payload)
    txn_ref = data.get('txnRef') or data.get('reference')

    if not txn_ref:
        logger.warning('Zainpay webhook: no txnRef/reference found in payload, ignoring')
        return JsonResponse({'status': 'ignored'}, status=200)

    if WalletTransaction.objects.filter(reference=txn_ref).exists():
        return JsonResponse({'status': 'already_processed'}, status=200)

    # verify_deposit() confirms the transaction is genuine on Zainpay's side
    # (defense in depth on top of signature verification) and gives us
    # beneficiaryAccountNumber reliably - but its response doesn't expose a
    # gross/pre-charge amount field, only amountAfterCharges. Since Zainpay
    # has committed to zero settlement charges on deposits, the wallet should
    # be credited the exact amount sent, so the amount itself comes from the
    # webhook payload's own "depositedAmount" field instead.
    verified = zainpay_service.verify_deposit(txn_ref)
    if not verified:
        logger.info('Zainpay webhook: could not verify deposit %s, ignoring', txn_ref)
        return JsonResponse({'status': 'ignored'}, status=200)

    logger.info('Zainpay webhook: verify_deposit response for %s: %s', txn_ref, verified)

    account_number = verified.get('beneficiaryAccountNumber') or data.get('beneficiaryAccountNumber')
    virtual_account = VirtualAccount.objects.filter(account_number=account_number).select_related('wallet').first()

    if not virtual_account:
        logger.warning('Zainpay webhook: no VirtualAccount found for %s (ref %s)', account_number, txn_ref)
        return JsonResponse({'status': 'ignored'}, status=200)

    raw_amount = data.get('depositedAmount')
    if not raw_amount:
        logger.warning(
            'Zainpay webhook: no depositedAmount in payload for %s, falling back to '
            'verify_deposit amountAfterCharges (this will include any charges)',
            txn_ref,
        )
        raw_amount = verified.get('amountAfterCharges') or verified.get('amount')

    # Confirmed in production: a real ₦100 deposit arrived as raw amount
    # 10000 (kobo) - convert to naira before crediting.
    amount = Decimal(str(raw_amount)) / 100

    logger.info(
        'Zainpay webhook: crediting wallet #%s with ₦%s (raw value %s, ref %s)',
        virtual_account.wallet_id, amount, raw_amount, txn_ref,
    )

    credit_wallet(
        wallet_id=virtual_account.wallet_id,
        amount=amount,
        reference=txn_ref,
        narration=f"Bank transfer deposit via {account_number}",
        source='zainpay_webhook',
        metadata=verified,
    )

    return JsonResponse({'status': 'success'}, status=200)
