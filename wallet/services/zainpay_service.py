import hashlib
import hmac
import logging
from decimal import ROUND_HALF_UP, Decimal

import requests
from django.conf import settings

from .exceptions import ZainpayCheckoutError, ZainpayTransferError

logger = logging.getLogger(__name__)


def _headers():
    # Zainpay's REST API authenticates with the public key as the Bearer
    # token (it's issued as a JWT) - the secret key is used separately, only
    # for verifying incoming webhook signatures (see verify_webhook_signature).
    return {
        "Authorization": f"Bearer {settings.ZAINPAY_PUBLIC_KEY}",
        "Content-Type": "application/json",
    }


def initialize_checkout(email, mobile_number, amount, txn_ref, callback_url, zainbox_code):
    """
    Starts a Zainpay hosted checkout session, restricted to bank transfer
    only (no card). Generic across whichever Zainbox the money should land
    in - the wallet-funding flow passes the wallet Zainbox, the admissions
    application-fee flow passes the school Zainbox. Returns the redirect URL
    the payer should be sent to; raises ZainpayCheckoutError on any failure.
    """
    # A plain string, confirmed against a known-working Zainpay integration
    # elsewhere. A JSON number was rejected outright ("expected a string"),
    # and a forced "500.00" was itself rejected as Invalid_amount - whole
    # amounts are sent without a decimal suffix (e.g. "500"), fractional
    # ones with exactly two (e.g. "767.75"), matching the API's own docs
    # example. Deliberately quantize rather than .normalize() here -
    # normalize() strips trailing zeros, so a fee-inclusive amount like
    # 5126.90 would come out as "5126.9" (one decimal digit) and get
    # rejected the same way a bare float was.
    amount_decimal = Decimal(str(amount))
    if amount_decimal == amount_decimal.to_integral_value():
        amount_str = str(int(amount_decimal))
    else:
        amount_str = str(amount_decimal.quantize(Decimal('0.01')))

    payload = {
        "amount": amount_str,
        "txnRef": txn_ref,
        "mobileNumber": mobile_number,
        "zainboxCode": zainbox_code,
        "emailAddress": email,
        "callBackUrl": callback_url,
        "paymentChannels": ["bank_transfer"],
    }

    logger.info('Zainpay checkout initialize request (ref %s): %s', txn_ref, payload)

    try:
        response = requests.post(
            f"{settings.ZAINPAY_BASE_URL}/zainbox/card/initialize/payment",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        logger.warning('Zainpay checkout initialize (ref %s) could not reach Zainpay: %s', txn_ref, e)
        raise ZainpayCheckoutError(f"Could not reach Zainpay: {e}")

    try:
        result = response.json()
    except ValueError:
        # Not a network failure - Zainpay responded, but the body wasn't
        # JSON (an HTML error page, an empty body, etc). Log the raw text
        # and status code, since that's the only way to see why.
        logger.warning(
            'Zainpay checkout initialize (ref %s) returned a non-JSON response: status=%s body=%r',
            txn_ref, response.status_code, response.text[:500],
        )
        raise ZainpayCheckoutError(f"Zainpay returned an unexpected response (status {response.status_code}).")

    logger.info('Zainpay checkout initialize response (ref %s): status=%s body=%s', txn_ref, response.status_code, result)

    if result.get('code') != '00' or not result.get('data'):
        reason = result.get('description') or 'Could not start Zainpay checkout.'
        logger.warning('Zainpay checkout initialize (ref %s) failed: %s | full response: %s', txn_ref, reason, result)
        raise ZainpayCheckoutError(reason)

    return result['data']


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

    return result.get('data') or []


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


def transfer_wallet_to_school(amount, txn_ref, narration):
    """
    Moves money from the wallet Zainbox to the school Zainbox for a
    fee-payment made from a parent's wallet. Zainpay infers this is a
    Zainbox-to-Zainbox move (not a real bank transfer) from the account
    numbers given - same /bank/transfer/v2 endpoint either way. Returns the
    transfer data dict (including totalTxnAmount/txnFee) on success, or
    raises ZainpayTransferError.
    """
    # Zainpay's transfer API expects amounts in KOBO (confirmed in production:
    # sending "900" transferred ₦9.00, not ₦900). Multiply naira by 100 and
    # send as a plain integer string - decimal format is also rejected.
    amount_kobo = int(Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) * 100)

    payload = {
        "destinationAccountNumber": settings.ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER,
        "destinationBankCode": settings.ZAINPAY_SCHOOL_SETTLEMENT_BANK_CODE,
        "amount": str(amount_kobo),
        "sourceAccountNumber": settings.ZAINPAY_WALLET_ACCOUNT_NUMBER,
        "sourceBankCode": settings.ZAINPAY_WALLET_BANK_CODE,
        "zainboxCode": settings.ZAINPAY_WALLET_ZAINBOX_CODE,
        "txnRef": txn_ref,
        "narration": narration,
        "callbackUrl": f"{settings.SITE_BASE_URL}/parent/webhooks/zainpay/",
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

    # `.get('data', {})` only falls back to {} when the key is *absent* - if
    # Zainpay returns the key with an explicit null (seen in production on a
    # rejected request), `.get()` still returns None and `.get('status')`
    # below would crash. `or {}` covers both cases.
    data = result.get('data') or {}

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
