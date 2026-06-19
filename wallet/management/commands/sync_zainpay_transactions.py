from django.core.management.base import BaseCommand

from wallet.models import VirtualAccount, WalletTransaction
from wallet.services import zainpay_service
from wallet.services.wallet_service import credit_wallet


class Command(BaseCommand):
    help = (
        "Polls Zainpay's transaction history for every active virtual account "
        "and credits any deposit that has no matching WalletTransaction - "
        "catching deposits whose webhook was never delivered. Safe to run "
        "repeatedly: credit_wallet() is idempotent on the transaction reference."
    )

    def handle(self, *args, **options):
        virtual_accounts = VirtualAccount.objects.select_related('wallet').all()
        credited = 0
        checked = 0

        for virtual_account in virtual_accounts:
            transactions = zainpay_service.list_account_transactions(virtual_account.account_number)

            for txn in transactions:
                if txn.get('transactionType') != 'deposit':
                    continue

                checked += 1
                reference = txn.get('transactionRef') or txn.get('reference')
                if not reference:
                    continue

                if WalletTransaction.objects.filter(reference=reference).exists():
                    continue

                amount = txn.get('amount')
                if not amount:
                    continue

                credit_wallet(
                    wallet_id=virtual_account.wallet_id,
                    amount=amount,
                    reference=reference,
                    narration=f"Bank transfer deposit via {virtual_account.account_number} (reconciled)",
                    source='zainpay_reconciliation',
                    metadata=txn,
                )
                credited += 1
                self.stdout.write(self.style.WARNING(
                    f"Credited missed deposit: wallet #{virtual_account.wallet_id}, "
                    f"reference={reference}, amount=₦{amount}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Checked {checked} deposit transaction(s) across {virtual_accounts.count()} "
            f"virtual account(s) - {credited} missed deposit(s) credited."
        ))
