from decimal import Decimal

from django.conf import settings

from wallet.services import zainpay_service

from .models import Applicant


def confirm_application_payment(reference):
    """
    Looks up one specific deposit by reference in the school Zainbox's
    transaction history and marks the matching Applicant as paid if found.
    Used by both apply_callback (immediate, on-redirect check) and as the
    backup path from wallet.webhooks.zainpay_deposit_webhook when the school
    Zainbox receives a deposit.success event - mirrors
    wallet_service.credit_zainpay_deposit_if_found exactly, reusing the same
    already-generic zainpay_service.list_account_transactions.

    Returns True if the applicant is now marked paid (or was already paid
    earlier - idempotent either way), False if Zainpay doesn't have a
    matching deposit yet or the reference isn't a known application.
    """
    applicant = Applicant.objects.filter(reference=reference).first()
    if not applicant:
        return False

    if applicant.application_fee_paid:
        return True

    transactions = zainpay_service.list_account_transactions(settings.ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER)
    matching = next(
        (t for t in transactions if t.get('transactionRef') == reference and t.get('transactionType') == 'deposit'),
        None,
    )
    if not matching:
        return False

    amount = Decimal(str(matching.get('amount', 0))) / 100
    applicant.application_fee_paid = True
    applicant.amount_paid = amount
    applicant.save(update_fields=['application_fee_paid', 'amount_paid'])
    return True
