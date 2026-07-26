from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from admissions.models import Applicant
from src.models import Section, SchoolClass, Session, Student
from wallet.test_support import seed_site_context_fixtures


class ApplicantApprovalTests(TestCase):
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

    def test_approve_creates_guardians_and_student(self):
        response = self.client.post(f'/school/applicants/{self.applicant.id}/', {'approve': '1'}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.status, 'approved')
        self.assertIsNotNone(self.applicant.linked_student)

        student = self.applicant.linked_student
        self.assertEqual(student.admission_status, 'not_admitted')
        self.assertEqual(student.first_name, 'Amina')
        self.assertEqual(student.enrolled_class, self.school_class)
        self.assertEqual(student.guardians.count(), 2)

        father = student.guardians.get(relationship='Father')
        self.assertEqual(father.phone_number, '08011112222')
        self.assertEqual(father.occupation, 'Trader')
        self.assertEqual(father.qualification, 'BSc')

        mother = student.guardians.get(relationship='Mother')
        self.assertEqual(mother.phone_number, '08033334444')

    def test_approve_uses_recommended_class_over_desired_class(self):
        other_class = SchoolClass.objects.create(
            name='KINDERGARTEN GIRLS', level='Nil', arm='A', section=self.school_class.section,
        )
        self.applicant.recommended_class = other_class
        self.applicant.save()

        self.client.post(f'/school/applicants/{self.applicant.id}/', {'approve': '1'})

        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.linked_student.enrolled_class, other_class)

    def test_approved_student_then_flows_through_not_admitted_queue(self):
        self.client.post(f'/school/applicants/{self.applicant.id}/', {'approve': '1'})
        self.applicant.refresh_from_db()
        student = self.applicant.linked_student

        response = self.client.get('/school/students/not-admitted/')
        self.assertContains(response, 'Amina')

        self.client.post('/school/students/not-admitted/', {
            'admit_selected': '1', 'student_ids': [student.id],
        }, follow=True)

        student.refresh_from_db()
        self.assertEqual(student.admission_status, 'admitted')
        self.assertIsNotNone(student.admission_number)

    def test_cannot_approve_twice(self):
        self.client.post(f'/school/applicants/{self.applicant.id}/', {'approve': '1'})
        student_count_after_first = Student.objects.count()

        self.client.post(f'/school/applicants/{self.applicant.id}/', {'approve': '1'})

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

    def test_applicant_review_requires_login(self):
        anon_client = Client()
        response = anon_client.get(f'/school/applicants/{self.applicant.id}/')
        self.assertNotEqual(response.status_code, 200)
