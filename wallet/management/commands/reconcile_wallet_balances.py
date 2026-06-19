from django.core.management.base import BaseCommand

from wallet.models import Wallet


class Command(BaseCommand):
    help = (
        "Compares each Wallet's cached balance against the authoritative "
        "balance derived from the WalletTransaction ledger (SUM(credits) - "
        "SUM(debits)). Reports any discrepancy found; never auto-corrects, "
        "since a mismatch indicates a bug that should be investigated, not "
        "silently papered over."
    )

    def handle(self, *args, **options):
        wallets = Wallet.objects.select_related('parent_account__user').all()
        mismatches = 0

        for wallet in wallets:
            authoritative = wallet.authoritative_balance
            if wallet.balance != authoritative:
                mismatches += 1
                self.stdout.write(self.style.ERROR(
                    f"MISMATCH: Wallet #{wallet.id} ({wallet.parent_account}) - "
                    f"cached balance=₦{wallet.balance}, ledger sum=₦{authoritative}, "
                    f"difference=₦{wallet.balance - authoritative}"
                ))

        if mismatches == 0:
            self.stdout.write(self.style.SUCCESS(
                f"Checked {wallets.count()} wallet(s) - all balances match their ledger."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Checked {wallets.count()} wallet(s) - {mismatches} mismatch(es) found. "
                f"Review and correct manually via the admin (always as a new ledger entry, never a direct edit)."
            ))
