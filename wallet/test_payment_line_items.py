from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from src.models import (
    FeeComponent,
    FeeStructure,
    OtherFeeStructure,
    Payment,
    PaymentLineItem,
    SchoolClass,
    Section,
    Session,
    Student,
    Term,
)
from wallet.services.payment_service import create_payment_record
from wallet.test_support import seed_site_context_fixtures


def make_fee_fixture():
    section = Section.objects.create(name='Secondary')
    school_class = SchoolClass.objects.create(name='JSS1', level='JSS 1', arm='A', section=section)
    session = Session.objects.create(
        name='2025/2026', start_date='2025-09-01', end_date='2026-07-31', current=True,
    )
    term = Term.objects.create(
        name='First Term', session=session, start_date='2025-09-01', end_date='2025-12-15',
    )
    student = Student.objects.create(
        admission_number='GIIA/0001', first_name='Chioma', last_name='Eze',
        enrolled_class=school_class,
    )
    fee_structure = FeeStructure.objects.create(
        section=section, session=session, term_group='first',
        student_type='new', transport=False, total_amount=Decimal('50000.00'),
    )
    FeeComponent.objects.create(fee_structure=fee_structure, name='Tuition', amount=Decimal('40000.00'), category='tuition')
    FeeComponent.objects.create(fee_structure=fee_structure, name='Feeding', amount=Decimal('10000.00'), category='feeding')

    other_fee = OtherFeeStructure.objects.create(
        name='TRANSPORTATION', amount=Decimal('15000.00'), session=session, term=term,
        category='transportation',
    )

    return {
        'section': section, 'school_class': school_class, 'session': session,
        'term': term, 'student': student, 'fee_structure': fee_structure,
        'other_fee': other_fee,
    }


class PaymentLineItemAllocationTests(TestCase):
    def setUp(self):
        self.fixture = make_fee_fixture()

    def test_full_school_fee_payment_splits_proportionally_and_sums_exactly(self):
        payment = create_payment_record(
            student=self.fixture['student'], amount=Decimal('50000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-FULL', fee_structure=self.fixture['fee_structure'],
        )
        line_items = list(payment.line_items.all().order_by('label'))
        self.assertEqual(len(line_items), 2)

        by_label = {li.label: li for li in line_items}
        self.assertEqual(by_label['Tuition'].amount, Decimal('40000.00'))
        self.assertEqual(by_label['Tuition'].category, 'tuition')
        self.assertEqual(by_label['Feeding'].amount, Decimal('10000.00'))
        self.assertTrue(all(li.is_estimated for li in line_items))

        total = sum((li.amount for li in line_items), Decimal('0.00'))
        self.assertEqual(total, payment.amount_paid)

    def test_partial_school_fee_payment_splits_proportionally_and_sums_exactly(self):
        # Pays 40% of the 50,000 bundle - each component should get ~40% of
        # its own amount, and the two shares must still sum to exactly what
        # was paid despite rounding.
        payment = create_payment_record(
            student=self.fixture['student'], amount=Decimal('20000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-PARTIAL', fee_structure=self.fixture['fee_structure'],
        )
        line_items = list(payment.line_items.all())
        total = sum((li.amount for li in line_items), Decimal('0.00'))
        self.assertEqual(total, Decimal('20000.00'))

        by_label = {li.label: li for li in line_items}
        self.assertEqual(by_label['Tuition'].amount, Decimal('16000.00'))  # 40% of 40,000
        self.assertEqual(by_label['Feeding'].amount, Decimal('4000.00'))   # 40% of 10,000

    def test_other_fee_payment_creates_single_exact_line_item(self):
        payment = create_payment_record(
            student=self.fixture['student'], amount=Decimal('15000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-OTHER', other_fee=self.fixture['other_fee'],
        )
        line_items = list(payment.line_items.all())
        self.assertEqual(len(line_items), 1)
        self.assertEqual(line_items[0].category, 'transportation')
        self.assertEqual(line_items[0].amount, Decimal('15000.00'))
        self.assertFalse(line_items[0].is_estimated)

    def test_retrying_payment_creation_does_not_duplicate_line_items(self):
        from wallet.services.payment_line_item_service import create_line_items_for_payment

        payment = create_payment_record(
            student=self.fixture['student'], amount=Decimal('50000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-RETRY', fee_structure=self.fixture['fee_structure'],
        )
        self.assertEqual(payment.line_items.count(), 2)
        create_line_items_for_payment(payment)  # simulate a retried call
        self.assertEqual(payment.line_items.count(), 2)


class PaymentListItemTypeFilterTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.fixture = make_fee_fixture()
        self.staff = User.objects.create_user(username='admin', password='pw', is_staff=True)
        self.client = Client()
        self.client.force_login(self.staff)

    def test_item_type_filter_finds_bundled_and_standalone_payments(self):
        bundled = create_payment_record(
            student=self.fixture['student'], amount=Decimal('50000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-A', fee_structure=self.fixture['fee_structure'],
        )
        create_payment_record(
            student=self.fixture['student'], amount=Decimal('15000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-B', other_fee=self.fixture['other_fee'],
        )

        response = self.client.get('/school/payments/', {'item_type': 'tuition'})
        self.assertEqual(response.status_code, 200)
        returned_ids = {p.id for p in response.context['payments']}
        self.assertEqual(returned_ids, {bundled.id})
        self.assertEqual(response.context['item_type_total'], Decimal('40000.00'))

    def test_item_type_filter_transportation_only_matches_transportation(self):
        create_payment_record(
            student=self.fixture['student'], amount=Decimal('50000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-C', fee_structure=self.fixture['fee_structure'],
        )
        transport_payment = create_payment_record(
            student=self.fixture['student'], amount=Decimal('15000.00'),
            payment_method='cash', session=self.fixture['session'], term=self.fixture['term'],
            transaction_reference='REF-D', other_fee=self.fixture['other_fee'],
        )

        response = self.client.get('/school/payments/', {'item_type': 'transportation'})
        returned_ids = {p.id for p in response.context['payments']}
        self.assertEqual(returned_ids, {transport_payment.id})
