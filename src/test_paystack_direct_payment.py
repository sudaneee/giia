from decimal import Decimal

from django.test import TestCase

from src.models import (
    FeeComponent,
    FeeStructure,
    OtherFeeStructure,
    Payment,
    PaymentBatch,
    SchoolClass,
    Section,
    Session,
    Student,
    Term,
)
from src.views import compute_student_fee_status, process_paystack_charge_success


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
        admission_number='GIIA/1001', first_name='Amaka', last_name='Obi',
        enrolled_class=school_class,
    )
    fee_structure = FeeStructure.objects.create(
        section=section, session=session, term_group='first',
        student_type='new', transport=False, total_amount=Decimal('50000.00'),
    )
    FeeComponent.objects.create(fee_structure=fee_structure, name='Tuition', amount=Decimal('50000.00'))

    return {
        'section': section, 'school_class': school_class, 'session': session,
        'term': term, 'student': student, 'fee_structure': fee_structure,
    }


def make_batch(session, term, reference='GIIA-TEST0001', payment_channel='card', amount=Decimal('50000.00')):
    return PaymentBatch.objects.create(
        reference=reference, parent_email='parent@example.com', amount_paid=amount,
        session=session, term=term, payment_channel=payment_channel, status='pending',
    )


def school_fee_charge(reference, fixture, amount=Decimal('50000.00'), channel='card'):
    return {
        'reference': reference,
        'amount': int(amount * 100),
        'channel': channel,
        'customer': {'email': 'parent@example.com'},
        'metadata': {
            'payment_data': {
                'fee_type': 'school_fees',
                'session_id': fixture['session'].id,
                'term_id': fixture['term'].id,
                'students': [{
                    'admission_number': fixture['student'].admission_number,
                    'amount': str(amount),
                    'fee_structure_id': fixture['fee_structure'].id,
                }],
            },
        },
    }


def other_fee_charge(reference, fixture, other_fees, channel='transfer'):
    total = sum(Decimal(str(f.amount)) for f in other_fees)
    return {
        'reference': reference,
        'amount': int(total * 100),
        'channel': channel,
        'customer': {'email': 'parent@example.com'},
        'metadata': {
            'payment_data': {
                'fee_type': 'other_fees',
                'session_id': fixture['session'].id,
                'term_id': fixture['term'].id,
                'students': [{
                    'admission_number': fixture['student'].admission_number,
                    'fees': [
                        {'id': f.id, 'amount': str(f.amount), 'name': f.name} for f in other_fees
                    ],
                }],
            },
        },
    }


class ProcessPaystackChargeSuccessTests(TestCase):
    def setUp(self):
        self.fixture = make_fee_fixture()

    def test_school_fees_creates_payment_with_fee_structure_and_normalized_method(self):
        batch = make_batch(self.fixture['session'], self.fixture['term'], payment_channel='card')
        charge = school_fee_charge(batch.reference, self.fixture)

        result_batch = process_paystack_charge_success(charge)

        self.assertEqual(result_batch.status, 'success')
        payment = Payment.objects.get(student=self.fixture['student'])
        self.assertEqual(payment.payment_method, 'credit_card')
        self.assertEqual(payment.fee_structure, self.fixture['fee_structure'])
        self.assertEqual(payment.amount_paid, Decimal('50000.00'))

    def test_transfer_channel_normalizes_to_bank_transfer(self):
        batch = make_batch(self.fixture['session'], self.fixture['term'], payment_channel='transfer')
        charge = school_fee_charge(batch.reference, self.fixture, channel='bank_transfer')

        process_paystack_charge_success(charge)

        payment = Payment.objects.get(student=self.fixture['student'])
        self.assertEqual(payment.payment_method, 'bank_transfer')

    def test_other_fees_creates_one_payment_per_fee_item_with_other_fee_set(self):
        cardigan = OtherFeeStructure.objects.create(
            name='Cardigan', amount=Decimal('5000.00'),
            session=self.fixture['session'], term=self.fixture['term'], active=True,
        )
        tahfeez = OtherFeeStructure.objects.create(
            name='Tahfeez Fee', amount=Decimal('3000.00'),
            session=self.fixture['session'], term=self.fixture['term'], active=True,
        )
        batch = make_batch(
            self.fixture['session'], self.fixture['term'],
            reference='GIIA-TEST0002', amount=Decimal('8000.00'),
        )
        charge = other_fee_charge(batch.reference, self.fixture, [cardigan, tahfeez])

        process_paystack_charge_success(charge)

        payments = Payment.objects.filter(student=self.fixture['student']).order_by('other_fee__name')
        self.assertEqual(payments.count(), 2)
        self.assertEqual(
            set(payments.values_list('other_fee__name', flat=True)),
            {'Cardigan', 'Tahfeez Fee'},
        )

    def test_calling_twice_does_not_duplicate_payments(self):
        batch = make_batch(self.fixture['session'], self.fixture['term'], reference='GIIA-TEST0003')
        charge = school_fee_charge(batch.reference, self.fixture)

        process_paystack_charge_success(charge)
        process_paystack_charge_success(charge)

        self.assertEqual(Payment.objects.filter(student=self.fixture['student']).count(), 1)

    def test_callback_and_webhook_racing_for_same_reference_credit_only_once(self):
        """
        Simulates the callback and the webhook both processing the same
        charge (as could happen if both arrive close together) by calling
        process_paystack_charge_success twice for the same reference before
        either has had a chance to see the other's write reflected as
        batch.status == 'success' - exactly one Payment must survive per
        student, and neither call should raise.
        """
        batch = make_batch(self.fixture['session'], self.fixture['term'], reference='GIIA-TEST0004')
        charge = school_fee_charge(batch.reference, self.fixture)

        # Force both calls past the "already success" short-circuit by
        # resetting status between them, mimicking two concurrent readers
        # that both observed 'pending' before either wrote 'success'.
        process_paystack_charge_success(charge)
        batch.refresh_from_db()
        batch.status = 'pending'
        batch.save(update_fields=['status'])
        process_paystack_charge_success(charge)

        self.assertEqual(Payment.objects.filter(student=self.fixture['student']).count(), 1)

    def test_no_matching_student_leaves_batch_pending_and_increments_attempts(self):
        batch = make_batch(self.fixture['session'], self.fixture['term'], reference='GIIA-TEST0005')
        charge = school_fee_charge(batch.reference, self.fixture)
        charge['metadata']['payment_data']['students'][0]['admission_number'] = 'GIIA/DOES-NOT-EXIST'

        result_batch = process_paystack_charge_success(charge)

        self.assertEqual(result_batch.status, 'pending')
        self.assertEqual(result_batch.webhook_attempts, 1)
        self.assertIsNotNone(result_batch.last_webhook_attempt)
        self.assertEqual(Payment.objects.count(), 0)

    def test_creates_batch_when_none_exists(self):
        charge = school_fee_charge('GIIA-NEWBATCH0001', self.fixture)

        result_batch = process_paystack_charge_success(charge)

        self.assertEqual(result_batch.status, 'success')
        self.assertTrue(PaymentBatch.objects.filter(reference='GIIA-NEWBATCH0001').exists())


class ComputeStudentFeeStatusWalletTests(TestCase):
    def test_wallet_payments_count_as_paid(self):
        fixture = make_fee_fixture()
        Payment.objects.create(
            student=fixture['student'], fee_structure=fixture['fee_structure'],
            amount_paid=Decimal('50000.00'), payment_method='wallet', status='paid',
            session=fixture['session'], term=fixture['term'],
        )

        result = compute_student_fee_status(
            fixture['student'], fixture['fee_structure'], fixture['session'], fixture['term'],
        )

        self.assertEqual(result['paid'], Decimal('50000.00'))
        self.assertEqual(result['status'], 'PAID')
