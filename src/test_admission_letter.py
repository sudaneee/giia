from decimal import Decimal
from io import BytesIO

from django.test import Client, TestCase
from pypdf import PdfReader

from src.models import FeeComponent, FeeStructure, SchoolClass, SchoolConfig, Section, Session, Student


class AdmissionLetterTests(TestCase):
    """
    generate_admission_letter is public/unauthenticated by design (see
    project memory) - these just check the PDF actually generates, since it
    was hand-edited (report-in dates, fees breakdown page) without any
    prior test coverage.
    """

    def setUp(self):
        self.section = Section.objects.create(name='KINDERGARTEN')
        self.school_class = SchoolClass.objects.create(
            name='KINDERGARTEN BOYS', level='Nil', arm='A', section=self.section,
        )
        self.session = Session.objects.create(
            name='2026/2027', start_date='2026-09-01', end_date='2027-07-31', current=True,
        )
        # Reuses a real on-disk image so the view's os.path.exists() checks pass.
        SchoolConfig.objects.create(header_image='pics/giialogo.png', signature_image='pics/giialogo.png')
        self.student = Student.objects.create(
            first_name='Amina', last_name='Bello', admission_status='admitted',
            admission_number='GIIA-2026-1', enrolled_class=self.school_class,
        )
        self.client = Client()

    def test_admission_letter_generates_with_fee_breakdown(self):
        fee_structure = FeeStructure.objects.create(
            section=self.section, session=self.session, term_group='first', student_type='new',
            transport=False, total_amount=Decimal('50000.00'),
        )
        FeeComponent.objects.create(fee_structure=fee_structure, name='Tuition', amount=Decimal('40000.00'))
        FeeComponent.objects.create(fee_structure=fee_structure, name='Feeding', amount=Decimal('10000.00'))

        response = self.client.get(f'/school/students/generate-admission-letter/{self.student.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        reader = PdfReader(BytesIO(response.content))
        self.assertEqual(len(reader.pages), 2)
        page2_text = reader.pages[1].extract_text()
        self.assertIn('FEES BREAKDOWN', page2_text)
        self.assertIn('Tuition', page2_text)
        self.assertIn('Total', page2_text)

        page1_text = reader.pages[0].extract_text().replace('\n', ' ')
        self.assertIn('OFFER OF ADMISSION', page1_text)
        self.assertIn('2026/2027', page1_text)
        self.assertIn('Amina Bello', page1_text)
        self.assertIn('31st August 2026', page1_text)
        self.assertIn('Yours faithfully', page1_text)

    def test_admission_letter_without_fee_structure_shows_fallback(self):
        response = self.client.get(f'/school/students/generate-admission-letter/{self.student.id}/')

        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.content))
        page2_text = reader.pages[1].extract_text()
        self.assertIn('not yet published', page2_text)

    def test_admission_letter_is_public(self):
        response = self.client.get(f'/school/students/generate-admission-letter/{self.student.id}/')
        self.assertEqual(response.status_code, 200)
