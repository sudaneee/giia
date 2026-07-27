import logging
from decimal import ROUND_CEILING, Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from wallet.services import zainpay_service

from .models import Applicant

logger = logging.getLogger(__name__)

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


def send_payment_confirmation_email(applicant):
    """
    Emails the parent a link to their application preview/receipt. Called
    from sync_admission_payments once it confirms a payment - best-effort,
    since a bad email address or a down mail server shouldn't stop the
    cronjob from finishing its reconciliation pass over the rest.
    """
    to_email = applicant.father_email or applicant.mother_email
    if not to_email:
        logger.info('No email on file for applicant %s - skipping confirmation email', applicant.app_number)
        return False

    receipt_url = settings.SITE_BASE_URL.rstrip('/') + reverse(
        'admissions:application_receipt', args=[applicant.reference],
    )

    try:
        send_mail(
            subject=f'GIIA Application {applicant.app_number} - Payment Confirmed',
            message=(
                f"Dear {applicant.father_name or applicant.mother_name},\n\n"
                f"We have received your application fee payment for "
                f"{applicant.first_name} {applicant.last_name} "
                f"(Application No: {applicant.app_number}).\n\n"
                f"You can view or print your application here:\n{receipt_url}\n\n"
                "Great Insight Int'l Academy Zaria"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception('Failed to send payment confirmation email for applicant %s', applicant.app_number)
        return False
