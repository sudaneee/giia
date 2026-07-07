import hashlib
import hmac
import logging
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"


def _headers():
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def calculate_topup_fee(amount, payment_method):
    """
    Same formula as calculate_paystack_fee in src/views.py (the existing
    direct fee-payment flow) - 0.7% capped at ₦1500 for card, flat ₦300 for
    bank_transfer/ussd. Duplicated intentionally rather than imported from
    that views module: this is a 4-line pure function, and importing service
    logic from a different app's views module would be worse coupling than a
    small, obviously-in-sync duplication.
    """
    amount = Decimal(str(amount))
    if payment_method == 'card':
        fee = amount * Decimal('0.007')
        return min(fee, Decimal('1500')).quantize(Decimal('0.01'))
    return Decimal('300.00')


def initialize_topup(email, amount, payment_method, reference, callback_url, metadata):
    """
    Initializes a Paystack Checkout transaction for a wallet top-up. `amount`
    here is the total naira amount to charge the parent (requested top-up
    amount + Paystack fee) - the caller is responsible for adding the fee
    before calling this. Returns the parsed response dict; the caller checks
    `status` and reads `data['authorization_url']` to redirect the parent.
    """
    channels = ['card'] if payment_method == 'card' else ['bank_transfer', 'ussd']

    try:
        response = requests.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize",
            headers=_headers(),
            json={
                "email": email,
                "amount": int(Decimal(str(amount)) * 100),
                "reference": reference,
                "channels": channels,
                "callback_url": callback_url,
                "metadata": metadata,
            },
            timeout=30,
        )
        return response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning('Paystack topup initialize (ref %s) could not reach Paystack: %s', reference, e)
        return {"status": False, "message": str(e)}


def verify_transaction(reference):
    """
    Confirms a transaction with Paystack directly, rather than trusting the
    callback/webhook payload alone. Returns the verified `data` dict, or None
    if it can't be confirmed successful.
    """
    try:
        response = requests.get(
            f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
            headers=_headers(),
            timeout=30,
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not result.get('status') or result.get('data', {}).get('status') != 'success':
        return None

    return result['data']


def verify_webhook_signature(raw_body, signature):
    """
    Paystack signs webhook payloads with HMAC-SHA512 of the raw body using
    the secret key (per Paystack's webhook docs) - matches what the existing
    direct fee-payment paystack_webhook in src/views.py already does.
    """
    if not signature:
        return False

    computed = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(computed, signature)
