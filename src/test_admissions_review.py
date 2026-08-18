from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from admissions.models import Applicant
from src.models import Section, SchoolClass, Session, Student
from wallet.test_support import seed_site_context_fixtures


class ApplicantAdmissionTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.staff_user = User.objects.create_user(username='staff1', password='TestPass123!', is_staff=True)
        self.session = Session.objects.create(
            name='2026/2027', start_date='2026-09-01', end_date='2027-07-31', current=True,
        )
        section = Section.objects.create(name='KINDERGARTEN')
        self.school_class = SchoolClass.objects.create(
            name='KINDERGARTEN BOYS', level='Nil', arm='A', section=section,
        )
        self.applicant = Applicant.objects.create(
            first_name='Amina', last_name='Bello', date_of_birth='2020-01-01', gender='Female',
            residential_address='1 Test Street',
            father_name='Musa Bello', father_phone='08011112222', father_email='musa@example.com',
            father_address='Zaria', father_occupation='Trader', father_qualification='BSc',
            mother_name='Aisha Bello', mother_phone='08033334444',
            session=self.session, desired_class=self.school_class,
            declaration_name='Musa Bello', declaration_agreed=True,
            reference='APPFEE-APPROVE1', application_fee_paid=True, amount_paid=Decimal('5000.00'),
        )
        self.client = Client()
        self.client.login(username='staff1', password='TestPass123!')

    def test_admit_from_detail_page_creates_guardians_and_admits_immediately(self):
        response = self.client.post(f'/school/applicants/{self.applicant.id}/', {'admit': '1'}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.status, 'approved')
        self.assertIsNotNone(self.applicant.linked_student)

        student = self.applicant.linked_student
        self.assertEqual(student.admission_status, 'admitted')
        self.assertIsNotNone(student.admission_number)
        self.assertIsNotNone(student.admitted_at)
        self.assertEqual(student.first_name, 'Amina')
        self.assertEqual(student.enrolled_class, self.school_class)
        self.assertEqual(student.guardians.count(), 2)

        father = student.guardians.get(relationship='Father')
        self.assertEqual(father.phone_number, '08011112222')
        self.assertEqual(father.occupation, 'Trader')
        self.assertEqual(father.qualification, 'BSc')

        mother = student.guardians.get(relationship='Mother')
        self.assertEqual(mother.phone_number, '08033334444')

    def test_admit_uses_recommended_class_over_desired_class(self):
        other_class = SchoolClass.objects.create(
            name='KINDERGARTEN GIRLS', level='Nil', arm='A', section=self.school_class.section,
        )
        self.applicant.recommended_class = other_class
        self.applicant.save()

        self.client.post(f'/school/applicants/{self.applicant.id}/', {'admit': '1'})

        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.linked_student.enrolled_class, other_class)

    def test_cannot_admit_twice(self):
        self.client.post(f'/school/applicants/{self.applicant.id}/', {'admit': '1'})
        student_count_after_first = Student.objects.count()

        self.client.post(f'/school/applicants/{self.applicant.id}/', {'admit': '1'})

        self.assertEqual(Student.objects.count(), student_count_after_first)

    def test_reject_marks_status_without_creating_student(self):
        self.client.post(f'/school/applicants/{self.applicant.id}/', {'reject': '1'}, follow=True)

        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.status, 'rejected')
        self.assertIsNone(self.applicant.linked_student)

    def test_record_interview(self):
        self.client.post(f'/school/applicants/{self.applicant.id}/', {
            'record_interview': '1', 'interview_date': '2026-08-01', 'interviewer_name': 'Mrs. Yusuf',
            'recommended_class': self.school_class.id,
        }, follow=True)

        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.status, 'interviewed')
        self.assertEqual(self.applicant.interviewer_name, 'Mrs. Yusuf')

    def test_applicant_list_only_shows_paid_applications(self):
        Applicant.objects.create(
            first_name='Unpaid', last_name='Applicant', date_of_birth='2020-01-01', gender='Male',
            residential_address='addr', father_name='Someone', father_phone='08000000000',
            session=self.session, desired_class=self.school_class,
            declaration_name='Someone', declaration_agreed=True,
            reference='APPFEE-UNPAID1', application_fee_paid=False,
        )

        response = self.client.get('/school/applicants/')

        self.assertContains(response, 'Amina')
        self.assertNotContains(response, 'Unpaid')

    def test_applicant_list_drops_admitted_applicants(self):
        self.client.post(f'/school/applicants/{self.applicant.id}/', {'admit': '1'})

        response = self.client.get('/school/applicants/')

        self.assertNotIn(self.applicant, response.context['applicants'])

    def test_applicant_list_scoped_to_session(self):
        other_session = Session.objects.create(
            name='2025/2026', start_date='2025-09-01', end_date='2026-07-31', current=False,
        )
        Applicant.objects.create(
            first_name='Lastyear', last_name='Kid', date_of_birth='2019-01-01', gender='Male',
            residential_address='addr', father_name='Someone', father_phone='08000000001',
            session=other_session, desired_class=self.school_class,
            declaration_name='Someone', declaration_agreed=True,
            reference='APPFEE-OLDSESSION', application_fee_paid=True,
        )

        response = self.client.get('/school/applicants/')
        self.assertContains(response, 'Amina')
        self.assertNotContains(response, 'Lastyear')

        response = self.client.get(f'/school/applicants/?session={other_session.id}')
        self.assertContains(response, 'Lastyear')
        self.assertNotContains(response, 'Amina')

    def test_bulk_admit_selected_from_applicant_list(self):
        second_applicant = Applicant.objects.create(
            first_name='Yusuf', last_name='Ibrahim', date_of_birth='2020-05-01', gender='Male',
            residential_address='addr', father_name='Ibrahim Yusuf', father_phone='08055556666',
            session=self.session, desired_class=self.school_class,
            declaration_name='Ibrahim Yusuf', declaration_agreed=True,
            reference='APPFEE-BULK2', application_fee_paid=True,
        )

        response = self.client.post('/school/applicants/', {
            'admit_selected': '1',
            'applicant_ids': [self.applicant.id, second_applicant.id],
            f'recommended_class_{self.applicant.id}': self.school_class.id,
            f'recommended_class_{second_applicant.id}': self.school_class.id,
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.applicant.refresh_from_db()
        second_applicant.refresh_from_db()

        self.assertEqual(self.applicant.linked_student.admission_status, 'admitted')
        self.assertEqual(second_applicant.linked_student.admission_status, 'admitted')
        self.assertNotEqual(
            self.applicant.linked_student.admission_number, second_applicant.linked_student.admission_number,
        )

    def test_applicant_review_requires_login(self):
        anon_client = Client()
        response = anon_client.get(f'/school/applicants/{self.applicant.id}/')
        self.assertNotEqual(response.status_code, 200)


class PublicAdmissionSearchTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.session = Session.objects.create(
            name='2026/2027', start_date='2026-09-01', end_date='2027-07-31', current=True,
        )
        section = Section.objects.create(name='KINDERGARTEN')
        self.school_class = SchoolClass.objects.create(
            name='KINDERGARTEN BOYS', level='Nil', arm='A', section=section,
        )
        self.student = Student.objects.create(
            first_name='Amina', last_name='Bello', admission_status='admitted',
            admission_number='GIIA-2026-1', enrolled_class=self.school_class,
            admitted_at='2026-09-15T10:00:00Z', phone_number='08011112222',
        )
        self.client = Client()

    def test_no_query_shows_no_results(self):
        response = self.client.get('/school/students/admitted/')
        self.assertNotContains(response, 'Amina')

    def test_search_by_name_finds_admitted_student(self):
        response = self.client.get('/school/students/admitted/', {'q': 'Amina'})
        self.assertContains(response, 'Amina')
        self.assertContains(response, 'GIIA-2026-1')

    def test_search_by_phone_finds_admitted_student(self):
        response = self.client.get('/school/students/admitted/', {'q': '08011112222'})
        self.assertContains(response, 'Amina')

    def test_search_does_not_find_non_admitted_student(self):
        Student.objects.create(
            first_name='Waiting', last_name='Kid', admission_status='not_admitted',
        )
        response = self.client.get('/school/students/admitted/', {'q': 'Waiting'})
        self.assertNotContains(response, 'Waiting Kid')

    def test_search_excludes_students_outside_current_session(self):
        self.student.admitted_at = '2020-01-01T10:00:00Z'
        self.student.save()

        response = self.client.get('/school/students/admitted/', {'q': 'Amina'})
        self.assertNotContains(response, 'GIIA-2026-1')

    def test_public_page_requires_no_login(self):
        response = self.client.get('/school/students/admitted/')
        self.assertEqual(response.status_code, 200)
