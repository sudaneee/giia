from django.core.management.base import BaseCommand
from django.db import transaction
from src.models import Student, Guardian, SchoolClass 

class Command(BaseCommand):
    help = 'Replaces data in default DB with data from source_db'

    def handle(self, *args, **options):
        source_db = 'source_db'
        dest_db = 'default'

        source_students = Student.objects.using(source_db).all()
        self.stdout.write(f"Syncing {source_students.count()} students (Overwriting destination)...")

        for src_student in source_students:
            if not src_student.admission_number:
                continue

            try:
                with transaction.atomic(using=dest_db):
                    # 1. Handle SchoolClass (Find or Create by Name)
                    dest_class = None
                    if src_student.enrolled_class_id:
                        src_class_data = SchoolClass.objects.using(source_db).filter(
                            id=src_student.enrolled_class_id
                        ).values('name').first()
                        
                        if src_class_data:
                            # We take the first match to avoid "MultipleObjects" errors
                            dest_class = SchoolClass.objects.using(dest_db).filter(
                                name=src_class_data['name']
                            ).first()

                            if not dest_class:
                                dest_class = SchoolClass.objects.using(dest_db).create(
                                    name=src_class_data['name']
                                )

                    # 2. Overwrite Student Data
                    # .update_or_create ensures existing records are updated to match source
                    student_defaults = {
                        'first_name': src_student.first_name,
                        'last_name': src_student.last_name,
                        'date_of_birth': src_student.date_of_birth,
                        'gender': src_student.gender,
                        'address': src_student.address,
                        'phone_number': src_student.phone_number,
                        'email': src_student.email,
                        'enrolled_class': dest_class,
                        'photo': src_student.photo,
                        'status': src_student.status,
                        'admission_status': src_student.admission_status,
                        'admitted_at': src_student.admitted_at,
                    }

                    student_obj, created = Student.objects.using(dest_db).update_or_create(
                        admission_number=src_student.admission_number,
                        defaults=student_defaults
                    )

                    # 3. Mirror Guardians (Replace existing links)
                    student_obj.guardians.clear()
                    for src_guardian in src_student.guardians.all():
                        dest_guardian = Guardian.objects.using(dest_db).filter(
                            email=src_guardian.email
                        ).first()

                        if not dest_guardian:
                            dest_guardian = Guardian.objects.using(dest_db).create(
                                email=src_guardian.email,
                                first_name=src_guardian.first_name,
                                last_name=src_guardian.last_name
                            )
                        
                        student_obj.guardians.add(dest_guardian)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed {src_student.admission_number}: {e}"))

        self.stdout.write(self.style.SUCCESS("Replacement Sync Complete."))