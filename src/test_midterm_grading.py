from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from src.midterm_grading import describe_label, get_midterm_bands, label_for_score
from src.models import MidTermResult, SchoolClass, Section, Session, Student, Subject, Term
from src.views import _build_midterm_result_data
from wallet.test_support import seed_site_context_fixtures


class MidtermGradingHelperTests(TestCase):
    def test_bands_defined_for_20_and_10(self):
        self.assertEqual(get_midterm_bands(20)[0], ('Emerging', 0, 6))
        self.assertEqual(get_midterm_bands(10)[0], ('Emerging', 0, 2))

    def test_unconfigured_max_score_raises_instead_of_guessing(self):
        with self.assertRaises(ValueError):
            get_midterm_bands(15)

    def test_boundary_that_was_previously_mismatched_is_now_correct(self):
        # Previously the template's "Overall Effort" used >=6/<9 etc instead
        # of the stated key's 0-6/7-9 - an average of exactly 6 landed in
        # "Emerging +" instead of "Emerging". label_for_score uses the same
        # bands as the per-row ticks, so this can't drift again.
        bands = get_midterm_bands(20)
        self.assertEqual(label_for_score(6, bands), 'Emerging')
        self.assertEqual(label_for_score(7, bands), 'Emerging +')
        self.assertEqual(label_for_score(9, bands), 'Emerging +')
        self.assertEqual(label_for_score(10, bands), 'Expected')

    def test_score_outside_every_band_has_no_label(self):
        bands = get_midterm_bands(20)
        self.assertIsNone(label_for_score(21, bands))
        self.assertEqual(describe_label(label_for_score(21, bands)), 'Invalid Score')

    def test_fractional_average_between_band_boundaries_rounds_to_nearest_band(self):
        # A multi-subject average (e.g. 67/8 = 8.375) rarely lands exactly on
        # an integer, but the bands are defined on whole numbers with gaps
        # between them (Expected + = 7-8, Exceeding = 9-10). 8.375 used to
        # match no band at all and show "Invalid Score" on real report
        # cards; it should round to the nearest band instead.
        bands = get_midterm_bands(10)
        self.assertEqual(label_for_score(8.375, bands), 'Expected +')  # rounds to 8
        self.assertEqual(label_for_score(8.625, bands), 'Exceeding')  # rounds to 9
        self.assertEqual(label_for_score(6.857142857142857, bands), 'Expected +')  # rounds to 7

    def test_describe_label_adds_the_descriptor(self):
        self.assertEqual(describe_label('Exceeding'), 'Exceeding (Excellent)')


class BuildMidtermResultDataTests(TestCase):
    def setUp(self):
        section = Section.objects.create(name='Primary')
        self.school_class = SchoolClass.objects.create(name='Basic 1', level='B1', arm='A', section=section)
        self.subject = Subject.objects.create(name='Maths')
        self.student = Student.objects.create(
            admission_number='GIIA-1', first_name='Ali', last_name='Musa', enrolled_class=self.school_class,
        )

    def test_session_with_max_20_uses_the_20_point_key(self):
        session = Session.objects.create(name='2025/2026', start_date=date(2025, 9, 1), end_date=date(2026, 7, 31), midterm_max_score=20)
        term = Term.objects.create(name='First Term', session=session, start_date=date(2025, 9, 1), end_date=date(2025, 12, 1))
        MidTermResult.objects.create(
            student=self.student, subject=self.subject, class_assigned=self.school_class,
            session=session, term=term, score=6, achievement='Fair',
        )
        data = _build_midterm_result_data(session, term, self.school_class, self.student)
        self.assertEqual(data['max_score'], 20)
        self.assertEqual(data['bands'][0], ('Emerging', 0, 6))
        self.assertEqual(data['overall_effort'], 'Emerging (Need Support)')

    def test_session_with_max_10_uses_the_10_point_key(self):
        session = Session.objects.create(name='2026/2027', start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), midterm_max_score=10)
        term = Term.objects.create(name='First Term', session=session, start_date=date(2026, 9, 1), end_date=date(2026, 12, 1))
        MidTermResult.objects.create(
            student=self.student, subject=self.subject, class_assigned=self.school_class,
            session=session, term=term, score=9, achievement='Great',
        )
        data = _build_midterm_result_data(session, term, self.school_class, self.student)
        self.assertEqual(data['max_score'], 10)
        self.assertEqual(data['overall_effort'], 'Exceeding (Excellent)')

    def test_new_session_defaults_to_max_20(self):
        session = Session.objects.create(name='2027/2028', start_date=date(2027, 9, 1), end_date=date(2028, 7, 31))
        self.assertEqual(session.midterm_max_score, 20)


class MidtermEntryValidationTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        section = Section.objects.create(name='Primary')
        self.school_class = SchoolClass.objects.create(name='Basic 1', level='B1', arm='A', section=section)
        self.session10 = Session.objects.create(name='2026/2027', start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), midterm_max_score=10)
        self.term = Term.objects.create(name='First Term', session=self.session10, start_date=date(2026, 9, 1), end_date=date(2026, 12, 1))
        self.subject = Subject.objects.create(name='Maths')
        self.student = Student.objects.create(
            admission_number='GIIA-1', first_name='Ali', last_name='Musa', enrolled_class=self.school_class,
        )
        self.staff = User.objects.create_user(username='staff', password='pw', is_staff=True)
        self.client = Client()
        self.client.force_login(self.staff)

    def test_manual_save_rejects_score_above_session_max(self):
        self.client.post(reverse('bulk_update_midterm_results'), {
            'session': self.session10.id, 'term': self.term.id,
            'class_assigned': self.school_class.id, 'subject': self.subject.id,
        })
        self.client.post(reverse('bulk_update_midterm_results'), {
            'session': self.session10.id, 'term': self.term.id,
            'class_assigned': self.school_class.id, 'subject': self.subject.id,
            'save_results': '1',
            f'score_{self.student.id}': '15',
            f'achievement_{self.student.id}': 'Great',
        })
        result = MidTermResult.objects.get(student=self.student, session=self.session10, term=self.term, subject=self.subject)
        self.assertEqual(result.score, 0)  # unchanged from the get_or_create default, not saved as 15

    def test_manual_save_accepts_score_within_session_max(self):
        self.client.post(reverse('bulk_update_midterm_results'), {
            'session': self.session10.id, 'term': self.term.id,
            'class_assigned': self.school_class.id, 'subject': self.subject.id,
        })
        self.client.post(reverse('bulk_update_midterm_results'), {
            'session': self.session10.id, 'term': self.term.id,
            'class_assigned': self.school_class.id, 'subject': self.subject.id,
            'save_results': '1',
            f'score_{self.student.id}': '9',
            f'achievement_{self.student.id}': 'Great',
        })
        result = MidTermResult.objects.get(student=self.student, session=self.session10, term=self.term, subject=self.subject)
        self.assertEqual(result.score, 9)


class SessionFormValidationTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.staff = User.objects.create_user(username='staff', password='pw', is_staff=True)
        self.client = Client()
        self.client.force_login(self.staff)

    def test_create_session_rejects_unconfigured_max_score(self):
        self.client.post(reverse('session_create'), {
            'name': '2028/2029', 'start_date': '2028-09-01', 'end_date': '2029-07-31', 'midterm_max_score': '15',
        })
        self.assertFalse(Session.objects.filter(name='2028/2029').exists())

    def test_create_session_accepts_configured_max_score(self):
        self.client.post(reverse('session_create'), {
            'name': '2028/2029', 'start_date': '2028-09-01', 'end_date': '2029-07-31', 'midterm_max_score': '10',
        })
        session = Session.objects.get(name='2028/2029')
        self.assertEqual(session.midterm_max_score, 10)
