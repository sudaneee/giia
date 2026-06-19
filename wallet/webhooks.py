import json
import logging

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
    signature = request.headers.get('x-zainpay-signature') or request.headers.get('X-Zainpay-Signature')

    if not zainpay_service.verify_webhook_signature(raw_body, signature):
        logger.warning('Zainpay webhook rejected: invalid signature')
        return JsonResponse({'error': 'Invalid signature'}, status=401)

    try:
        payload = json.loads(raw_body)
    except ValueError:
        return JsonResponse({'status': 'ignored'}, status=200)

    data = payload.get('data', payload)
    txn_ref = data.get('txnRef') or data.get('reference')

    if not txn_ref:
        return JsonResponse({'status': 'ignored'}, status=200)

    if WalletTransaction.objects.filter(reference=txn_ref).exists():
        return JsonResponse({'status': 'already_processed'}, status=200)

    verified = zainpay_service.verify_deposit(txn_ref)
    if not verified:
        logger.info('Zainpay webhook: could not verify deposit %s, ignoring', txn_ref)
        return JsonResponse({'status': 'ignored'}, status=200)

    account_number = verified.get('beneficiaryAccountNumber')
    virtual_account = VirtualAccount.objects.filter(account_number=account_number).select_related('wallet').first()

    if not virtual_account:
        logger.warning('Zainpay webhook: no VirtualAccount found for %s (ref %s)', account_number, txn_ref)
        return JsonResponse({'status': 'ignored'}, status=200)

    amount = verified.get('amountAfterCharges') or verified.get('amount')

    credit_wallet(
        wallet_id=virtual_account.wallet_id,
        amount=amount,
        reference=txn_ref,
        narration=f"Bank transfer deposit via {account_number}",
        source='zainpay_webhook',
        metadata=verified,
    )

    return JsonResponse({'status': 'success'}, status=200)
