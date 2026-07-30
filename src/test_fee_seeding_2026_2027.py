from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from src.models import FeeComponent, FeeStructure, Section, Session


def make_sections():
    for name in ["KINDERGARTEN", "RECEPTION", "BASIC 1-6", "JSS 1-3"]:
        Section.objects.get_or_create(name=name)


class SeedFees20262027Tests(TestCase):
    def setUp(self):
        make_sections()
        call_command('seed_fees_2026_2027', stdout=StringIO())
        self.session = Session.objects.get(name='2026/2027')

    def _structure(self, section_name, term_group, student_type, transport=False):
        return FeeStructure.objects.get(
            session=self.session, section__name=section_name,
            term_group=term_group, student_type=student_type, transport=transport,
        )

    def test_new_intake_first_term_includes_uniform(self):
        fs = self._structure('KINDERGARTEN', 'first', 'new')
        names = set(fs.components.values_list('name', flat=True))
        self.assertIn('Uniform', names)
        self.assertIn('Learning Materials', names)
        self.assertEqual(fs.total_amount, Decimal('69748.00'))

    def test_returning_first_term_includes_learning_materials_excludes_uniform(self):
        fs = self._structure('KINDERGARTEN', 'first', 'returning')
        names = set(fs.components.values_list('name', flat=True))
        self.assertIn('Learning Materials', names)
        self.assertNotIn('Uniform', names)
        # new_first (69748.00) minus Uniform (13000.00)
        self.assertEqual(fs.total_amount, Decimal('56748.00'))

    def test_returning_first_term_with_transport(self):
        fs = self._structure('KINDERGARTEN', 'first', 'returning', transport=True)
        self.assertEqual(fs.total_amount, Decimal('56748.00') + Decimal('45000.00'))

    def test_second_term_has_neither_learning_materials_nor_uniform_for_either_type(self):
        for student_type in ['new', 'returning']:
            fs = self._structure('KINDERGARTEN', 'second', student_type)
            names = set(fs.components.values_list('name', flat=True))
            self.assertNotIn('Learning Materials', names)
            self.assertNotIn('Uniform', names)
            self.assertEqual(fs.total_amount, Decimal('41548.00'))

    def test_basic_1_6_returning_first_term_total(self):
        fs = self._structure('BASIC 1-6', 'first', 'returning')
        # Tuition 42630.90 + Learning Materials 21500.00 + Feeding 20800.00 + TA Fees 7000.00
        self.assertEqual(fs.total_amount, Decimal('91930.90'))


class FixReturningFirstTermFeesCommandTests(TestCase):
    """
    Simulates the pre-fix buggy state that's already live on the server
    (returning/first-term FeeStructures missing their Learning Materials
    component) and confirms the standalone correction command repairs it.
    """
    def setUp(self):
        make_sections()
        self.session = Session.objects.create(
            name='2026/2027', start_date='2026-09-01', end_date='2027-07-31', current=True,
        )
        self.section = Section.objects.get(name='KINDERGARTEN')
        self.fee_structure = FeeStructure.objects.create(
            section=self.section, session=self.session, term_group='first',
            student_type='returning', transport=False, total_amount=Decimal('41548.00'),
        )
        FeeComponent.objects.create(fee_structure=self.fee_structure, name='Tuition', amount=Decimal('20748.00'))
        FeeComponent.objects.create(fee_structure=self.fee_structure, name='Feeding', amount=Decimal('20800.00'))

    def test_adds_missing_component_and_recalculates_total(self):
        call_command('fix_returning_first_term_fees_2026_2027', stdout=StringIO())

        self.fee_structure.refresh_from_db()
        names = set(self.fee_structure.components.values_list('name', flat=True))
        self.assertIn('Learning Materials', names)
        self.assertEqual(
            self.fee_structure.components.get(name='Learning Materials').amount, Decimal('15200.00'),
        )
        self.assertEqual(self.fee_structure.total_amount, Decimal('56748.00'))

    def test_safe_to_run_twice(self):
        call_command('fix_returning_first_term_fees_2026_2027', stdout=StringIO())
        call_command('fix_returning_first_term_fees_2026_2027', stdout=StringIO())

        self.fee_structure.refresh_from_db()
        self.assertEqual(
            self.fee_structure.components.filter(name='Learning Materials').count(), 1,
        )
        self.assertEqual(self.fee_structure.total_amount, Decimal('56748.00'))

    def test_does_not_touch_new_intake_or_other_terms(self):
        other_fs = FeeStructure.objects.create(
            section=self.section, session=self.session, term_group='first',
            student_type='new', transport=False, total_amount=Decimal('69748.00'),
        )
        FeeComponent.objects.create(fee_structure=other_fs, name='Tuition', amount=Decimal('20748.00'))
        FeeComponent.objects.create(fee_structure=other_fs, name='Learning Materials', amount=Decimal('15200.00'))
        FeeComponent.objects.create(fee_structure=other_fs, name='Feeding', amount=Decimal('20800.00'))
        FeeComponent.objects.create(fee_structure=other_fs, name='Uniform', amount=Decimal('13000.00'))

        call_command('fix_returning_first_term_fees_2026_2027', stdout=StringIO())

        other_fs.refresh_from_db()
        self.assertEqual(other_fs.total_amount, Decimal('69748.00'))
