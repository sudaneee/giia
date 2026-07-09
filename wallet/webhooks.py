import json
import logging
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import WalletFundingRequest, WalletTransaction
from .services import paystack_service, zainpay_service
from .services.wallet_service import credit_wallet

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def zainpay_deposit_webhook(request):
    """
    Single callback URL registered against both Zainboxes in the Zainpay
    dashboard (the wallet Zainbox and the school Zainbox), so this handles:
      - deposit.success on the wallet Zainbox: a parent completed a Zainpay
        Checkout top-up - look up the WalletFundingRequest by txnRef to find
        which wallet and how much, then credit it.
      - deposit.success on the school Zainbox: just the wallet-to-school fee
        payment transfer landing on the other side - no wallet action
        needed, pay_school_fees_from_wallet already debited the ledger
        synchronously off the transfer API's own response.
      - transfer.success / transfer.failed: logged only, for the same reason
        - the transfer leg is driven synchronously, not by this event.
    Always returns 200 on the happy path so Zainpay doesn't endlessly retry -
    the one exception is an invalid signature, which gets a 401.
    """
    raw_body = request.body
    # Per Zainpay's webhook docs, the signature arrives in a header literally
    # named "Zainpay-Signature" (Django's header lookup is case-insensitive).
    signature = request.headers.get('Zainpay-Signature')

    # Logged unconditionally, before the signature check, so a rejection
    # still leaves us full visibility into what Zainpay actually sent.
    logger.info(
        'Zainpay webhook inbound - body: %s | extracted signature: %s',
        raw_body.decode('utf-8', errors='replace'), signature,
    )

    if not zainpay_service.verify_webhook_signature(raw_body, signature):
        logger.warning('Zainpay webhook rejected: invalid signature.')
        return JsonResponse({'error': 'Invalid signature'}, status=401)

    try:
        payload = json.loads(raw_body)
    except ValueError:
        return JsonResponse({'status': 'ignored'}, status=200)

    event = payload.get('event')
    data = payload.get('data', payload)

    if event in ('transfer.success', 'transfer.failed'):
        logger.info(
            'Zainpay webhook: %s for txnRef %s - handled synchronously in '
            'pay_school_fees_from_wallet, logging only',
            event, data.get('txnRef'),
        )
        return JsonResponse({'status': 'ignored'}, status=200)

    if event != 'deposit.success':
        logger.info('Zainpay webhook: ignoring unrecognized event "%s"', event)
        return JsonResponse({'status': 'ignored'}, status=200)

    zainbox_code = data.get('zainboxCode')
    if zainbox_code != settings.ZAINPAY_WALLET_ZAINBOX_CODE:
        logger.info(
            'Zainpay webhook: deposit.success on zainbox %s is not the wallet '
            'zainbox, ignoring (this is the school zainbox receiving a fee payment)',
            zainbox_code,
        )
        return JsonResponse({'status': 'ignored'}, status=200)

    txn_ref = data.get('txnRef')
    if not txn_ref:
        logger.warning('Zainpay webhook: no txnRef found in payload, ignoring')
        return JsonResponse({'status': 'ignored'}, status=200)

    if WalletTransaction.objects.filter(reference=txn_ref).exists():
        return JsonResponse({'status': 'already_processed'}, status=200)

    funding_request = WalletFundingRequest.objects.filter(reference=txn_ref).select_related('wallet').first()
    if not funding_request:
        logger.warning('Zainpay webhook: no WalletFundingRequest found for txnRef %s, ignoring', txn_ref)
        return JsonResponse({'status': 'ignored'}, status=200)

    raw_amount = data.get('depositedAmount')
    # Confirmed in production (prior integration): a real ₦100 deposit
    # arrived as raw amount 10000 (kobo) - convert to naira before crediting.
    # Fall back to the amount the parent actually requested only if Zainpay's
    # payload is missing the field entirely.
    amount = Decimal(str(raw_amount)) / 100 if raw_amount else funding_request.amount

    logger.info(
        'Zainpay webhook: crediting wallet #%s with ₦%s (raw value %s, ref %s)',
        funding_request.wallet_id, amount, raw_amount, txn_ref,
    )

    credit_wallet(
        wallet_id=funding_request.wallet_id,
        amount=amount,
        reference=txn_ref,
        narration='Wallet funding via Zainpay',
        source='zainpay_checkout_webhook',
        metadata=data,
    )

    return JsonResponse({'status': 'success'}, status=200)


@csrf_exempt
@require_POST
def paystack_funding_webhook(request):
    """
    Backup path for wallet-funding confirmation: normally
    wallet_fund_callback (the browser redirect back from Paystack Checkout)
    credits the wallet, but if the parent closes the tab before that redirect
    completes, this webhook is what actually gets the wallet credited.
    credit_wallet()'s reference-based idempotency means whichever of the two
    paths runs first wins - the other is a no-op.
    """
    raw_body = request.body
    signature = request.headers.get('x-paystack-signature')

    logger.info(
        'Paystack funding webhook inbound - headers: %s | body: %s',
        dict(request.headers), raw_body.decode('utf-8', errors='replace'),
    )

    if not paystack_service.verify_webhook_signature(raw_body, signature):
        logger.warning('Paystack funding webhook rejected: invalid signature.')
        return JsonResponse({'error': 'Invalid signature'}, status=401)

    try:
        payload = json.loads(raw_body)
    except ValueError:
        return JsonResponse({'status': 'ignored'}, status=200)

    if payload.get('event') != 'charge.success':
        return JsonResponse({'status': 'ignored'}, status=200)

    data = payload.get('data', {})
    metadata = data.get('metadata') or {}

    if metadata.get('purpose') != 'wallet_funding':
        return JsonResponse({'status': 'ignored'}, status=200)

    reference = data.get('reference')
    if not reference:
        return JsonResponse({'status': 'ignored'}, status=200)

    if WalletTransaction.objects.filter(reference=reference).exists():
        return JsonResponse({'status': 'already_processed'}, status=200)

    credit_wallet(
        wallet_id=metadata['wallet_id'],
        amount=metadata['requested_amount'],
        reference=reference,
        narration='Wallet funding via Paystack',
        source='paystack_webhook',
        metadata=data,
    )

    return JsonResponse({'status': 'success'}, status=200)
