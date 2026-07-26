from decimal import ROUND_CEILING, Decimal

from django.conf import settings

from wallet.services import zainpay_service

from .models import Applicant

# Zainpay's transfer-checkout charge: 1.5% + NGN50 per transaction.
ZAINPAY_FEE_PERCENTAGE = Decimal('0.015')
ZAINPAY_FEE_FLAT = Decimal('50')


def total_with_zainpay_charge(amount):
    """
    Zainpay deducts its 1.5% + NGN50 charge from the transferred amount
    itself before settling to the school Zainbox - it isn't billed to the
    school separately. So simply adding the charge on top of the base fee
    (amount * 1.5% + 50) isn't enough: Zainpay would then take its cut of
    the *bumped-up* total, and the school would net slightly less than the
    intended fee. Instead we gross up: solve for G such that
    G - (G * 1.5% + 50) == amount, i.e. G == (amount + 50) / (1 - 1.5%).
    That's the amount actually collected at checkout - the applicant
    absorbs the charge, and the school still nets the full base fee.

    Rounded UP to a whole naira - confirmed in production that Zainpay's
    checkout-initialize endpoint rejects any decimal amount at all
    ("Invalid_amount", even for a cleanly-formatted "5126.90"), so this
    can't be sent as cents. Rounding up (not to nearest) guarantees the
    school still nets at least the full base fee after Zainpay's cut.
    """
    amount = Decimal(str(amount))
    gross = (amount + ZAINPAY_FEE_FLAT) / (Decimal('1') - ZAINPAY_FEE_PERCENTAGE)
    return gross.quantize(Decimal('1'), rounding=ROUND_CEILING)


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
