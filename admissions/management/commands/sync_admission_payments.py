from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from admissions.models import Applicant
from wallet.services import zainpay_service


class Command(BaseCommand):
    help = (
        "Safety net for missed admission-application-fee webhooks: polls "
        "Zainpay's own transaction history for the school Zainbox's account "
        "and marks any Applicant paid whose reference matches a deposit but "
        "isn't marked paid yet. Safe to run repeatedly."
    )

    def handle(self, *args, **options):
        transactions = zainpay_service.list_account_transactions(settings.ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER)
        deposits = [t for t in transactions if t.get('transactionType') == 'deposit']

        credited = 0
        skipped_known = 0
        skipped_unmatched = 0

        for txn in deposits:
            reference = txn.get('transactionRef')
            if not reference:
                continue

            applicant = Applicant.objects.filter(reference=reference).first()
            if not applicant:
                skipped_unmatched += 1
                continue

            if applicant.application_fee_paid:
                skipped_known += 1
                continue

            amount = Decimal(str(txn.get('amount', 0))) / 100
            applicant.application_fee_paid = True
            applicant.amount_paid = amount
            applicant.save(update_fields=['application_fee_paid', 'amount_paid'])
            credited += 1
            self.stdout.write(self.style.SUCCESS(
                f"Marked application {applicant.app_number} paid: +₦{amount}"
            ))

        self.stdout.write(
            f"Checked {len(deposits)} deposit(s): {credited} newly marked paid, "
            f"{skipped_known} already known, {skipped_unmatched} unmatched."
        )
