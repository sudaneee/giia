from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from src.models import Payment
from wallet.services.payment_line_item_service import create_line_items_for_payment


class Command(BaseCommand):
    help = (
        "One-time backfill: creates PaymentLineItem rows for existing Payment "
        "records that predate the item-type breakdown feature, so historical "
        "payments become filterable/reportable by item type too. Purely "
        "additive - no Payment row, amount, or status is touched, only new "
        "PaymentLineItem rows are created. Safe to re-run: payments that "
        "already have line items are skipped unless --force is given. Always "
        "run with --dry-run first and review the summary before committing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be created without writing anything.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Rebuild line items even for payments that already have them "
                 "(e.g. after a categorization fix). Off by default so re-runs are cheap and idempotent.",
        )
        parser.add_argument(
            "--batch-size", type=int, default=500,
            help="How many Payment rows to process per DB transaction (default 500).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        batch_size = options["batch_size"]

        candidates = Payment.objects.filter(
            Q(fee_structure__isnull=False) | Q(other_fee__isnull=False)
        )
        if not force:
            candidates = candidates.filter(line_items__isnull=True)
        candidates = candidates.order_by("id")

        total = candidates.count()
        self.stdout.write(f"{'[DRY RUN] ' if dry_run else ''}Found {total} payment(s) to process (force={force}).")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        processed = 0
        skipped_no_source = 0
        ids = list(candidates.values_list("id", flat=True))

        for start in range(0, len(ids), batch_size):
            batch_ids = ids[start:start + batch_size]
            with transaction.atomic():
                for payment in Payment.objects.filter(id__in=batch_ids).select_related("fee_structure", "other_fee"):
                    if not payment.fee_structure_id and not payment.other_fee_id:
                        skipped_no_source += 1
                        continue
                    if dry_run:
                        processed += 1
                        continue
                    create_line_items_for_payment(payment)
                    processed += 1
                if dry_run:
                    # Nothing was actually written in dry-run mode, so there's
                    # nothing to roll back - this is just belt-and-braces.
                    transaction.set_rollback(True)

            self.stdout.write(f"  ...{min(start + batch_size, len(ids))}/{len(ids)}")

        self.stdout.write(self.style.SUCCESS(
            f"{'Would process' if dry_run else 'Processed'} {processed} payment(s). "
            f"Skipped {skipped_no_source} with neither fee_structure nor other_fee set "
            f"(nothing to categorize - these stay outside the item-type filter)."
        ))
        if dry_run:
            self.stdout.write("Re-run without --dry-run to write the line items.")
