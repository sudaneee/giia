from django.core.management.base import BaseCommand

from admissions.models import AdmissionOpening
from src.models import Session


class Command(BaseCommand):
    help = (
        "One-off cleanup: the initial seed opened admission for every arm of "
        "every Kindergarten/JSS 1-3 class, but the school only wants one arm "
        "(A) accepting applications this cycle. Closes (is_open=False) every "
        "currently-open AdmissionOpening for the current session whose class "
        "arm isn't 'A', leaving exactly one open option per class name. Safe "
        "to run more than once - only touches openings that are still open."
    )

    def handle(self, *args, **options):
        session = Session.objects.filter(current=True).first()
        if not session:
            self.stdout.write(self.style.ERROR("No current session set - nothing to do."))
            return

        openings = AdmissionOpening.objects.filter(
            session=session, is_open=True,
        ).exclude(school_class__arm='A').select_related('school_class')

        closed = 0
        for opening in openings:
            self.stdout.write(f"Closing {opening.school_class} (arm {opening.school_class.arm})")
            opening.is_open = False
            opening.save(update_fields=['is_open'])
            closed += 1

        self.stdout.write(self.style.SUCCESS(
            f"Closed {closed} non-A-arm admission opening(s) for session '{session.name}'."
        ))
