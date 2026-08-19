from io import BytesIO

from django.test import Client, TestCase
from pypdf import PdfReader

from src.models import SchoolClass, SchoolConfig, Section, Session, Student


class AdmissionLetterTests(TestCase):
    """
    generate_admission_letter is public/unauthenticated by design (see
    project memory) - these just check the PDF actually generates, since it
    was hand-edited (offer-of-admission wording, no fees breakdown page)
    without any prior test coverage.
    """

    def setUp(self):
        self.section = Section.objects.create(name='KINDERGARTEN')
        self.school_class = SchoolClass.objects.create(
            name='KINDERGARTEN BOYS', level='Nil', arm='A', section=self.section,
        )
        Session.objects.create(
            name='2026/2027', start_date='2026-09-01', end_date='2027-07-31', current=True,
        )
        # Reuses a real on-disk image so the view's os.path.exists() checks pass.
        SchoolConfig.objects.create(header_image='pics/giialogo.png', signature_image='pics/giialogo.png')
        self.student = Student.objects.create(
            first_name='Amina', last_name='Bello', admission_status='admitted',
            admission_number='GIIA-2026-1', enrolled_class=self.school_class,
        )
        self.client = Client()

    def test_admission_letter_content(self):
        response = self.client.get(f'/school/students/generate-admission-letter/{self.student.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        reader = PdfReader(BytesIO(response.content))
        self.assertEqual(len(reader.pages), 1)

        text = reader.pages[0].extract_text().replace('\n', ' ')
        self.assertIn('OFFER OF ADMISSION', text)
        self.assertIn('2026/2027', text)
        self.assertIn('Amina Bello', text)
        self.assertIn('31st August 2026', text)
        self.assertIn('Yours faithfully', text)
        self.assertIn('Head of School', text)
        self.assertNotIn('FEES BREAKDOWN', text)

    def test_admission_letter_signature_block_not_cut_off_by_a_long_name_and_class(self):
        long_class = SchoolClass.objects.create(
            name='UPPER PRIMARY TAHFEEZ AND ISLAMIC STUDIES SPECIAL CLASS', level='Nil', arm='A',
            section=self.section,
        )
        student = Student.objects.create(
            first_name='Abdurrahman Muhammad Al-Ameen', last_name='Sulaiman-Abdulkareem',
            admission_status='admitted', admission_number='GIIA-2026-2', enrolled_class=long_class,
        )

        response = self.client.get(f'/school/students/generate-admission-letter/{student.id}/')

        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.content))
        # Whichever page it lands on, the signature block must be complete somewhere.
        full_text = ' '.join(page.extract_text().replace('\n', ' ') for page in reader.pages)
        self.assertIn('Yours faithfully', full_text)
        self.assertIn('Ustz. Aliyu Ibrahim Yerima', full_text)
        self.assertIn('Head of School', full_text)

    def test_admission_letter_is_public(self):
        response = self.client.get(f'/school/students/generate-admission-letter/{self.student.id}/')
        self.assertEqual(response.status_code, 200)
