from decimal import Decimal

from django.core.management.base import BaseCommand

from src.models import FeeComponent, FeeStructure, Session

# Matches seed_fees_2026_2027's per-section "new_first" Learning Materials
# amount - the one component seed_fees_2026_2027 originally left out of a
# returning student's First Term fee.
LEARNING_MATERIALS_BY_SECTION = {
    "KINDERGARTEN": "15200.00",
    "RECEPTION": "15200.00",
    "BASIC 1-6": "21500.00",
    "JSS 1-3": "61000.00",
}


class Command(BaseCommand):
    help = (
        "Fixes a bug in seed_fees_2026_2027: a RETURNING student's First "
        "Term fee was missing the Learning Materials component. Per the "
        "approved circular, Learning Materials still applies to everyone in "
        "First Term - Uniform is the only cost that's new-intake-only. Adds "
        "the missing FeeComponent and recalculates FeeStructure.total_amount "
        "for every returning/first-term FeeStructure in the 2026/2027 "
        "session already on this database. Safe to run more than once."
    )

    def handle(self, *args, **options):
        session = Session.objects.filter(name="2026/2027").first()
        if not session:
            self.stdout.write(self.style.ERROR("Session '2026/2027' not found - nothing to fix."))
            return

        structures = FeeStructure.objects.filter(
            session=session, term_group="first", student_type="returning",
        ).select_related("section")

        fixed = 0
        for fee_structure in structures:
            section_name = fee_structure.section.name if fee_structure.section else None
            amount = LEARNING_MATERIALS_BY_SECTION.get(section_name)
            if amount is None:
                self.stdout.write(self.style.WARNING(
                    f"No Learning Materials amount known for section '{section_name}' (FeeStructure id={fee_structure.id}) - skipped."
                ))
                continue

            _, created = FeeComponent.objects.update_or_create(
                fee_structure=fee_structure, name="Learning Materials",
                defaults={"amount": Decimal(amount)},
            )

            new_total = sum(c.amount for c in fee_structure.components.all())
            if fee_structure.total_amount != new_total:
                fee_structure.total_amount = new_total
                fee_structure.save(update_fields=["total_amount"])

            fixed += 1
            self.stdout.write(
                f"{section_name} (transport={fee_structure.transport}): "
                f"Learning Materials {'added' if created else 'updated'} (+{amount}), "
                f"total_amount now {new_total}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Fixed {fixed} returning/first-term FeeStructure(s) for session '{session.name}'."
        ))
