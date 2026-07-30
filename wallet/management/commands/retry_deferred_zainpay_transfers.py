from django.core.management.base import BaseCommand
from django.utils import timezone

from wallet.models import DeferredZainpayTransfer
from wallet.services import zainpay_service
from wallet.services.exceptions import ZainpayTransferError


class Command(BaseCommand):
    help = (
        "Run this once you've topped up the wallet Zainbox with enough real "
        "funds. Retries every pending DeferredZainpayTransfer - a real "
        "Zainbox-to-Zainbox transfer that was skipped earlier because the "
        "wallet Zainbox didn't have enough real money, even though the "
        "parent's payment was already let through on their internal wallet "
        "ledger balance. Marks each one completed once its transfer "
        "actually succeeds. Safe to run repeatedly - only pending rows are "
        "touched, and a transfer that fails again (still insufficient "
        "funds, or any other reason) is simply left pending for next time."
    )

    def handle(self, *args, **options):
        pending = DeferredZainpayTransfer.objects.filter(completed=False).select_related(
            'wallet_payment__wallet_transaction__wallet__parent_account',
        )

        settled = 0
        still_pending = 0

        for deferred in pending:
            try:
                zainpay_service.transfer_wallet_to_school(
                    deferred.amount, deferred.txn_ref, deferred.narration,
                )
            except ZainpayTransferError as e:
                still_pending += 1
                self.stdout.write(self.style.WARNING(
                    f"{deferred.txn_ref}: still failing (₦{deferred.amount}) - {e}"
                ))
                continue

            deferred.completed = True
            deferred.completed_at = timezone.now()
            deferred.save(update_fields=['completed', 'completed_at'])
            settled += 1
            self.stdout.write(self.style.SUCCESS(
                f"{deferred.txn_ref}: transfer completed (₦{deferred.amount})"
            ))

        self.stdout.write(
            f"Checked {settled + still_pending} deferred transfer(s): {settled} settled, {still_pending} still pending."
        )
