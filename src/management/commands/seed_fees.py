from django.core.management.base import BaseCommand
from decimal import Decimal

from src.models import FeeStructure, FeeComponent
from src.models import Section, Session


class Command(BaseCommand):
    help = "Seed fee structures using rule-based generation (realistic intake rules)"

    def handle(self, *args, **kwargs):

        session = Session.objects.get(name="2025/2026")

        sections = {
            "KINDERGARTEN": Section.objects.get(name="KINDERGARTEN"),
            "RECEPTION": Section.objects.get(name="RECEPTION"),
            "BASIC 1-6": Section.objects.get(name="BASIC 1-6"),
            "JSS 1-3": Section.objects.get(name="JSS 1-3"),
        }

        base_fees = {
            "KINDERGARTEN": {
                "tuition": 15960,
                "feeding": 12000,
                "learning": 12000,
                "uniform": 11500,
                "transport": 30000,
            },
            "RECEPTION": {
                "tuition": 30923,
                "feeding": 12000,
                "learning": 12000,
                "uniform": 11500,
                "transport": 30000,
            },
            "BASIC 1-6": {
                "tuition": 32793,
                "feeding": 12000,
                "learning": 20000,
                "uniform": 11500,
                "ta": 5000,
                "transport": 36000,
            },
            "JSS 1-3": {
                "tuition": 65819,
                "feeding": 15000,
                "learning": 51312,
                "uniform": 18898,
                "ta": 9600,
                "transport": 45000,
            },
        }

        term_groups = ["first", "second", "third"]
        student_types = ["new", "returning"]
        transport_options = [True, False]

        created_count = 0

        for section_key, section in sections.items():
            fees = base_fees[section_key]

            for term_group in term_groups:
                for student_type in student_types:
                    for transport in transport_options:

                        components = []

                        # Always
                        components.append(("Tuition", fees["tuition"]))
                        components.append(("Feeding", fees["feeding"]))

                        if "ta" in fees:
                            components.append(("TA Fees", fees["ta"]))

                        # New intake (ANY TERM)
                        if student_type == "new":
                            components.append(("Learning Materials", fees["learning"]))
                            components.append(("Uniform", fees["uniform"]))

                        # Optional transport
                        if transport:
                            components.append(("Transport", fees["transport"]))

                        total_amount = sum(Decimal(amount) for _, amount in components)

                        fee_structure, created = FeeStructure.objects.get_or_create(
                            section=section,
                            session=session,
                            term_group=term_group,
                            student_type=student_type,
                            transport=transport,
                            defaults={
                                "total_amount": total_amount,
                                "description": "Auto-generated from approved fee policy",
                            },
                        )

                        if created:
                            created_count += 1
                            for name, amount in components:
                                FeeComponent.objects.create(
                                    fee_structure=fee_structure,
                                    name=name,
                                    amount=Decimal(amount),
                                )

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Seeding complete: {created_count} FeeStructures created"
            )
        )
