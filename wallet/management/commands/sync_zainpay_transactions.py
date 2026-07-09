from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from wallet.models import WalletFundingRequest, WalletTransaction
from wallet.services import zainpay_service
from wallet.services.wallet_service import credit_wallet


class Command(BaseCommand):
    help = (
        "Safety net for missed Zainpay deposit webhooks: polls Zainpay's own "
        "transaction history for the wallet Zainbox's pooled account and "
        "credits any deposit that has a matching WalletFundingRequest but no "
        "WalletTransaction yet. Safe to run repeatedly - credit_wallet() is "
        "idempotent on reference, and this also pre-filters already-known "
        "references before ever calling it."
    )

    def handle(self, *args, **options):
        transactions = zainpay_service.list_account_transactions(settings.ZAINPAY_WALLET_ACCOUNT_NUMBER)
        deposits = [t for t in transactions if t.get('transactionType') == 'deposit']

        credited = 0
        skipped_known = 0
        skipped_unmatched = 0

        for txn in deposits:
            reference = txn.get('transactionRef')
            if not reference:
                continue

            if WalletTransaction.objects.filter(reference=reference).exists():
                skipped_known += 1
                continue

            funding_request = WalletFundingRequest.objects.filter(reference=reference).first()
            if not funding_request:
                # Not a wallet top-up (could be some other deposit into this
                # account) - nothing for us to credit.
                skipped_unmatched += 1
                continue

            amount = Decimal(str(txn.get('amount', 0))) / 100
            credit_wallet(
                wallet_id=funding_request.wallet_id,
                amount=amount,
                reference=reference,
                narration='Wallet funding via Zainpay (reconciled - webhook did not arrive)',
                source='zainpay_reconcile',
                metadata=txn,
            )
            credited += 1
            self.stdout.write(self.style.SUCCESS(
                f"Credited missed deposit {reference}: wallet #{funding_request.wallet_id} +₦{amount}"
            ))

        self.stdout.write(
            f"Checked {len(deposits)} deposit(s): {credited} credited, "
            f"{skipped_known} already recorded, {skipped_unmatched} unmatched."
        )
