import hashlib
import hmac
import logging

import requests
from django.conf import settings
from django.db import IntegrityError

from wallet.models import VirtualAccount

from .exceptions import VirtualAccountCreationError, ZainpayTransferError

logger = logging.getLogger(__name__)


def _headers():
    # Zainpay's REST API authenticates with the public key as the Bearer
    # token (it's issued as a JWT) - the secret key is used separately, only
    # for verifying incoming webhook signatures (see verify_webhook_signature).
    return {
        "Authorization": f"Bearer {settings.ZAINPAY_PUBLIC_KEY}",
        "Content-Type": "application/json",
    }


def create_virtual_account(parent_account):
    """
    Creates a Zainpay static virtual account for this parent and stores it.
    Called lazily from the wallet activation view, never automatically at
    registration, so accounts only get created for parents who actually fund.
    """
    wallet = parent_account.wallet

    existing = VirtualAccount.objects.filter(wallet=wallet).first()
    if existing:
        return existing

    user = parent_account.user
    payload = {
        "bankType": "zainBank",
        "firstName": user.first_name,
        "surname": user.last_name,
        "email": user.email,
        "mobileNumber": parent_account.phone_number,
        "dob": parent_account.date_of_birth.strftime("%d-%m-%Y"),
        "gender": parent_account.gender,
        "address": parent_account.address,
        "title": parent_account.title,
        "state": parent_account.state,
        "bvn": parent_account.bvn,
        "zainboxCode": settings.ZAINPAY_ZAINBOX_CODE,
    }

    try:
        response = requests.post(
            f"{settings.ZAINPAY_BASE_URL}/virtual-account/create/request",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        result = response.json()
    except (requests.RequestException, ValueError) as e:
        raise VirtualAccountCreationError(f"Could not reach Zainpay: {e}")

    if response.status_code != 200 or result.get('code') != '00':
        raise VirtualAccountCreationError(result.get('description', 'Virtual account creation failed.'))

    data = result['data']

    try:
        return VirtualAccount.objects.create(
            wallet=wallet,
            account_number=data['accountNumber'],
            account_name=data['accountName'],
            bank_name=data.get('bankName', 'zainBank'),
            bank_code=settings.ZAINPAY_BANK_CODE,
        )
    except IntegrityError:
        # Another request for this same parent won the race and already
        # created the account - return that one instead of erroring.
        existing = VirtualAccount.objects.filter(wallet=wallet).first()
        if existing:
            return existing
        raise


def compute_webhook_signature(raw_body):
    """
    HMAC-SHA256 of the raw body using the secret key, per Zainpay's webhook
    documentation: events arrive with a "Zainpay-Signature" header containing
    an HmacSHA256 hash of the payload signed with the secret key.
    """
    return hmac.new(
        settings.ZAINPAY_SECRET_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_signature(raw_body, received_signature):
    if not received_signature:
        return False

    return hmac.compare_digest(compute_webhook_signature(raw_body), received_signature)


def list_account_transactions(account_number):
    """
    Fetches all transactions Zainpay has recorded against a virtual account.
    Used by the sync_zainpay_transactions reconciliation command to catch
    deposits that never produced a webhook call.
    """
    try:
        response = requests.get(
            f"{settings.ZAINPAY_BASE_URL}/virtual-account/wallet/transactions/{account_number}",
            headers=_headers(),
            timeout=30,
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        return []

    if result.get('code') != '00':
        return []

    return result.get('data', [])


def verify_deposit(txn_ref):
    """
    Independently confirms a deposit reported by the webhook, using Zainpay's
    deposit verification endpoint. Returns the verified data dict, or None if
    the reference can't be confirmed (caller should not credit in that case).
    """
    try:
        response = requests.get(
            f"{settings.ZAINPAY_BASE_URL}/virtual-account/wallet/deposit/verify/v2/{txn_ref}",
            headers=_headers(),
            timeout=30,
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        return None

    if result.get('code') != '00':
        return None

    return result['data']


def transfer_to_school(source_account_number, amount, txn_ref, narration):
    """
    Moves money from a parent's virtual account to the school's settlement
    account for a school-fee payment. Returns the transfer data dict
    (including totalTxnAmount/txnFee) on success, or raises ZainpayTransferError.
    """
    payload = {
        "destinationAccountNumber": settings.ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER,
        "destinationBankCode": settings.ZAINPAY_SCHOOL_SETTLEMENT_BANK_CODE,
        "amount": str(amount),
        "sourceAccountNumber": source_account_number,
        "sourceBankCode": settings.ZAINPAY_BANK_CODE,
        "zainboxCode": settings.ZAINPAY_ZAINBOX_CODE,
        "txnRef": txn_ref,
        "narration": narration,
        "callbackUrl": f"{settings.SITE_BASE_URL}/parent/webhooks/zainpay/transfer/",
    }

    logger.info('Zainpay transfer request (ref %s): %s', txn_ref, payload)

    try:
        response = requests.post(
            f"{settings.ZAINPAY_BASE_URL}/bank/transfer/v2",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        result = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning('Zainpay transfer (ref %s) could not reach Zainpay: %s', txn_ref, e)
        raise ZainpayTransferError(f"Could not reach Zainpay: {e}")

    logger.info('Zainpay transfer response (ref %s): status=%s body=%s', txn_ref, response.status_code, result)

    data = result.get('data', {})

    if data.get('status') != 'success':
        reason = data.get('failureReason') or result.get('description') or 'Transfer failed.'
        logger.warning('Zainpay transfer (ref %s) failed: %s | full response: %s', txn_ref, reason, result)
        raise ZainpayTransferError(reason)

    return data


def verify_transfer(txn_ref):
    """
    Fallback check for a transfer whose initial response was ambiguous
    (timeout, connection error, etc). Returns the verified data dict, or
    None if the transaction cannot be found/confirmed.
    """
    try:
        response = requests.get(
            f"{settings.ZAINPAY_BASE_URL}/virtual-account/wallet/transaction/verify/{txn_ref}",
            headers=_headers(),
            timeout=30,
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        return None

    if result.get('code') != '00':
        return None

    return result['data']
