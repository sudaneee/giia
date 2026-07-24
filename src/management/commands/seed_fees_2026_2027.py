from decimal import Decimal

from django.core.management.base import BaseCommand

from src.models import FeeStructure, FeeComponent, OtherFeeStructure, Section, Session, Term


class Command(BaseCommand):
    help = (
        "One-time setup for the 2026/2027 session: creates the Session and its "
        "three Terms (marking 2026/2027 as current), seeds FeeStructure/"
        "FeeComponent records from the approved fee circular dated 17 July "
        "2026, and carries over the existing Other Fees (cardigans, Tahfeez, "
        "transportation deposit) to the new session's Second Term. Safe to "
        "run more than once - everything is created with get_or_create/"
        "update_or_create keyed on its natural identity, so re-running just "
        "fills in whatever is still missing rather than duplicating."
    )

    def handle(self, *args, **options):
        session, _ = Session.objects.update_or_create(
            name="2026/2027",
            defaults={"start_date": "2026-09-01", "end_date": "2027-07-31", "current": True},
        )
        Session.objects.exclude(pk=session.pk).update(current=False)

        term_defs = [
            ("First Term", "2026-09-01", "2026-12-11"),
            ("Second Term", "2027-01-04", "2027-04-01"),
            ("Third Term", "2027-04-19", "2027-07-30"),
        ]
        terms = {}
        for name, start, end in term_defs:
            term, _ = Term.objects.get_or_create(
                session=session, name=name,
                defaults={"start_date": start, "end_date": end},
            )
            terms[name] = term

        sections = {
            name: Section.objects.get(name=name)
            for name in ["KINDERGARTEN", "RECEPTION", "BASIC 1-6", "JSS 1-3"]
        }

        # "new_first": what a NEW-INTAKE student pays only in First Term
        # (Learning Materials + Uniform, bought once - not repeated in later
        # terms, unlike the 2025/2026 policy this replaces). "base": every
        # other combination - returning students in any term, and new-intake
        # students from Second Term onward.
        fee_data = {
            "KINDERGARTEN": {
                "new_first": {"Tuition": "20748.00", "Learning Materials": "15200.00", "Feeding": "20800.00", "Uniform": "13000.00"},
                "base": {"Tuition": "20748.00", "Feeding": "20800.00"},
                "transport": "45000.00",
            },
            "RECEPTION": {
                "new_first": {"Tuition": "40199.90", "Learning Materials": "15200.00", "Feeding": "20800.00", "Uniform": "13000.00"},
                "base": {"Tuition": "40199.90", "Feeding": "20800.00"},
                "transport": "45000.00",
            },
            "BASIC 1-6": {
                "new_first": {"Tuition": "42630.90", "Learning Materials": "21500.00", "Feeding": "20800.00", "Uniform": "18000.00", "TA Fees": "7000.00"},
                "base": {"Tuition": "42630.90", "Feeding": "20800.00", "TA Fees": "7000.00"},
                "transport": "60000.00",
            },
            "JSS 1-3": {
                "new_first": {"Tuition": "85564.70", "Learning Materials": "61000.00", "Feeding": "26000.00", "Uniform": "21000.00", "TA Fees": "13440.00"},
                "base": {"Tuition": "85564.70", "Feeding": "26000.00", "TA Fees": "13440.00"},
                "transport": "70000.00",
            },
        }

        created_count = 0

        for section_name, section in sections.items():
            data = fee_data[section_name]

            for term_group in ["first", "second", "third"]:
                for student_type in ["new", "returning"]:
                    for transport in [True, False]:
                        is_new_first_term = student_type == "new" and term_group == "first"
                        components = dict(data["new_first"] if is_new_first_term else data["base"])
                        if transport:
                            components["Transport"] = data["transport"]

                        total_amount = sum(Decimal(v) for v in components.values())

                        fee_structure, created = FeeStructure.objects.get_or_create(
                            section=section, session=session, term_group=term_group,
                            student_type=student_type, transport=transport,
                            defaults={
                                "total_amount": total_amount,
                                "description": "2026/2027 approved fee circular (17 Jul 2026)",
                            },
                        )

                        if created:
                            created_count += 1
                            for name, amount in components.items():
                                FeeComponent.objects.create(
                                    fee_structure=fee_structure, name=name, amount=Decimal(amount),
                                )

        other_fees = [
            ("KG & RECEPTION CARDIGAN", Decimal("12000.00")),
            ("BASIC 1-3 CARDIGAN", Decimal("13000.00")),
            ("BASIC 4-6 CARDIGAN", Decimal("14000.00")),
            ("JSS 1 CARDIGAN", Decimal("16000.00")),
            ("TAHFEEZ", Decimal("10000.00")),
            ("TRANSPORTATION", Decimal("15000.00")),
        ]
        other_created = 0
        for name, amount in other_fees:
            _, created = OtherFeeStructure.objects.get_or_create(
                name=name, session=session, term=terms["Second Term"],
                defaults={"amount": amount, "active": True},
            )
            if created:
                other_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done: session '{session.name}' set as current, {len(terms)} term(s) ready, "
            f"{created_count} FeeStructure(s) created, {other_created} OtherFeeStructure(s) created."
        ))
