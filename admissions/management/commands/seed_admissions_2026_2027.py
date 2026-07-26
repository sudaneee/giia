from django.core.management.base import BaseCommand

from admissions.models import AdmissionOpening
from src.models import Section, SchoolClass, Session


class Command(BaseCommand):
    help = (
        "One-time setup for the 2026/2027 online admission cycle. Assigns "
        "the existing Tahfeez classes to a new TAHFEEZ Section (creating "
        "them if they don't exist on this database yet), creates JSS 3 "
        "boys/girls classes (JSS 1-3 currently only goes up to JSS 2), and "
        "opens admission for every Kindergarten and JSS 1-3 class (capacity "
        "30/10 respectively) plus the four Tahfeez classes (unlimited) for "
        "the 2026/2027 session. Review the AdmissionOpening rows in the "
        "admin afterward and close (is_open=False) any class that shouldn't "
        "actually be accepting applications. Safe to run more than once."
    )

    def handle(self, *args, **options):
        session, _ = Session.objects.get_or_create(
            name="2026/2027",
            defaults={"start_date": "2026-09-01", "end_date": "2027-07-31", "current": True},
        )

        tahfeez_section, _ = Section.objects.get_or_create(name="TAHFEEZ")
        jss_section, _ = Section.objects.get_or_create(name="JSS 1-3")

        # Match the existing Tahfeez classes by name rather than id (ids
        # will differ between databases) - create them if this database
        # doesn't have them yet.
        tahfeez_class_defs = [
            (["Umar", "Boys"], "Umar Ibn Khaddaab (عمر ابن الخطاب) Boys"),
            (["Umar", "Girls"], "Umar Ibn Khaddaab (عمر ابن الخطاب) Girls"),
            (["Siddiqq", "Boys"], "Abubakr - As - Siddiqq (أبو بكر الصديق) Boys"),
            (["Siddiqq", "Girls"], "Abubakr - As - Siddiqq (أبو بكر الصديق) Girls"),
        ]
        tahfeez_classes = []
        for keywords, default_name in tahfeez_class_defs:
            qs = SchoolClass.objects.all()
            for keyword in keywords:
                qs = qs.filter(name__icontains=keyword)
            school_class = qs.first()
            if not school_class:
                school_class = SchoolClass.objects.create(name=default_name, level="Nil", arm="A")
                self.stdout.write(f"Created Tahfeez class: {school_class}")
            if school_class.section_id != tahfeez_section.id:
                school_class.section = tahfeez_section
                school_class.save(update_fields=["section"])
            tahfeez_classes.append(school_class)

        # "Basic 7-9" = JSS 1-3, but only JSS 1/JSS 2 exist today.
        for gender in ["BOYS", "GIRLS"]:
            school_class, created = SchoolClass.objects.get_or_create(
                name=f"JSS 3 {gender}", section=jss_section,
                defaults={"level": "Nil", "arm": "A"},
            )
            if created:
                self.stdout.write(f"Created class: {school_class}")

        kg_classes = list(SchoolClass.objects.filter(section__name="KINDERGARTEN"))
        jss_classes = list(SchoolClass.objects.filter(section__name="JSS 1-3"))

        opened = 0
        for school_class in kg_classes:
            _, created = AdmissionOpening.objects.get_or_create(
                session=session, school_class=school_class, defaults={"capacity": 30},
            )
            opened += created

        for school_class in jss_classes:
            _, created = AdmissionOpening.objects.get_or_create(
                session=session, school_class=school_class, defaults={"capacity": 10},
            )
            opened += created

        for school_class in tahfeez_classes:
            _, created = AdmissionOpening.objects.get_or_create(
                session=session, school_class=school_class, defaults={"capacity": None},
            )
            opened += created

        self.stdout.write(self.style.SUCCESS(
            f"Done: session '{session.name}' ready, {len(kg_classes)} Kindergarten class(es), "
            f"{len(jss_classes)} JSS 1-3 class(es), {len(tahfeez_classes)} Tahfeez class(es) - "
            f"{opened} new AdmissionOpening row(s) created. Review in the admin and close any "
            f"class that shouldn't be accepting applications."
        ))
