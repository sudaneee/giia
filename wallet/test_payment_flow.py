import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from src.models import (
    FeeComponent,
    FeeStructure,
    OtherFeeStructure,
    Payment,
    SchoolClass,
    Section,
    Session,
    Student,
    Term,
)
from wallet.models import ParentAccount, ParentStudentLink, VirtualAccount, Wallet, WalletPayment, WalletTransaction
from wallet.services import fee_service, wallet_service
from wallet.services.exceptions import InsufficientFundsError, ZainpayTransferError
from wallet.services.wallet_service import credit_wallet
from wallet.test_zainpay import seed_site_context_fixtures


def make_parent_account(email='fee-parent@example.com'):
    user = User.objects.create_user(
        username=email, email=email, password='TestPass123!',
        first_name='Tunde', last_name='Okafor',
    )
    return ParentAccount.objects.create(
        user=user,
        phone_number='08033334444',
        title='Mr',
        gender='M',
        date_of_birth=datetime.date(1980, 1, 1),
        bvn='98765432109',
        address='2 Test Avenue',
        state='Lagos',
    )


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
    FeeComponent.objects.create(fee_structure=fee_structure, name='Tuition', amount=Decimal('40000.00'))
    FeeComponent.objects.create(fee_structure=fee_structure, name='Feeding', amount=Decimal('10000.00'))

    return {
        'section': section, 'school_class': school_class, 'session': session,
        'term': term, 'student': student, 'fee_structure': fee_structure,
    }


def mock_transfer_success(amount):
    # Zainpay returns amounts in kobo: ₦300 fee = 30000 kobo.
    amount_d = Decimal(str(amount))
    total_kobo = int((amount_d + Decimal('300')) * 100)
    return {
        'status': 'success',
        'amount': str(int(amount_d * 100)),
        'totalTxnAmount': str(total_kobo),
        'txnFee': '30000',
        'txnRef': 'whatever',
    }


@override_settings(ZAINPAY_TRANSFER_FEE_ESTIMATE=Decimal('300'))
class FeeServiceOutstandingTests(TestCase):
    def setUp(self):
        self.fixture = make_fee_fixture()

    def test_calculate_school_fee_outstanding_for_unpaid_student(self):
        result = fee_service.calculate_school_fee_outstanding(
            self.fixture['student'], self.fixture['session'], self.fixture['term'],
            term_group='first', student_type='new', transport=False,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['total_fee'], Decimal('50000.00'))
        self.assertEqual(result['paid'], Decimal('0.00'))
        self.assertEqual(result['balance'], Decimal('50000.00'))

    def test_calculate_school_fee_outstanding_deducts_prior_payment(self):
        Payment.objects.create(
            student=self.fixture['student'], fee_structure=self.fixture['fee_structure'],
            amount_paid=Decimal('20000.00'), payment_method='card', status='paid',
            session=self.fixture['session'], term=self.fixture['term'],
        )
        result = fee_service.calculate_school_fee_outstanding(
            self.fixture['student'], self.fixture['session'], self.fixture['term'],
            term_group='first', student_type='new', transport=False,
        )
        self.assertEqual(result['paid'], Decimal('20000.00'))
        self.assertEqual(result['balance'], Decimal('30000.00'))

    def test_calculate_school_fee_outstanding_returns_none_for_wrong_student_type(self):
        # The fixture's FeeStructure is student_type='new' - selecting
        # 'returning' should find no matching fee structure.
        result = fee_service.calculate_school_fee_outstanding(
            self.fixture['student'], self.fixture['session'], self.fixture['term'],
            term_group='first', student_type='returning', transport=False,
        )
        self.assertIsNone(result)


@override_settings(
    ZAINPAY_TRANSFER_FEE_ESTIMATE=Decimal('300'),
    ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER='9999999999',
)
class PayFromWalletServiceTests(TestCase):
    def setUp(self):
        self.fixture = make_fee_fixture()
        self.parent_account = make_parent_account()
        ParentStudentLink.objects.create(parent_account=self.parent_account, student=self.fixture['student'])
        self.wallet = self.parent_account.wallet
        self.virtual_account = VirtualAccount.objects.create(
            wallet=self.wallet, account_number='1112223334', account_name='Tunde Okafor', bank_name='zainBank',
        )

    def _fee_selections(self):
        return [{
            'student': self.fixture['student'],
            'amount': Decimal('50000.00'),
            'fee_structure': self.fixture['fee_structure'],
        }]

    @patch('wallet.services.wallet_service.zainpay_service.transfer_to_school')
    def test_successful_payment_debits_wallet_and_creates_payment(self, mock_transfer):
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'fund-1', 'Funding', 'zainpay_webhook')
        mock_transfer.return_value = mock_transfer_success(Decimal('50000.00'))

        wallet_payment = wallet_service.pay_school_fees_from_wallet(
            self.parent_account, self._fee_selections(), self.fixture['session'], self.fixture['term'],
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00') - Decimal('50300.00'))
        self.assertEqual(self.wallet.balance, self.wallet.authoritative_balance)

        self.assertEqual(wallet_payment.payments.count(), 1)
        payment = wallet_payment.payments.first()
        self.assertEqual(payment.payment_method, 'wallet')
        self.assertEqual(payment.amount_paid, Decimal('50000.00'))
        self.assertEqual(payment.student, self.fixture['student'])

        mock_transfer.assert_called_once()
        call_args = mock_transfer.call_args[0]
        self.assertEqual(call_args[0], '1112223334')

    @patch('wallet.services.wallet_service.zainpay_service.transfer_to_school')
    def test_insufficient_balance_never_calls_zainpay(self, mock_transfer):
        credit_wallet(self.wallet.id, Decimal('1000.00'), 'fund-2', 'Funding', 'zainpay_webhook')

        with self.assertRaises(InsufficientFundsError):
            wallet_service.pay_school_fees_from_wallet(
                self.parent_account, self._fee_selections(), self.fixture['session'], self.fixture['term'],
            )

        mock_transfer.assert_not_called()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000.00'))

    @patch('wallet.services.wallet_service.zainpay_service.transfer_to_school')
    def test_transfer_failure_leaves_no_trace_in_ledger(self, mock_transfer):
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'fund-3', 'Funding', 'zainpay_webhook')
        mock_transfer.side_effect = ZainpayTransferError('destination bank not responding')

        with self.assertRaises(ZainpayTransferError):
            wallet_service.pay_school_fees_from_wallet(
                self.parent_account, self._fee_selections(), self.fixture['session'], self.fixture['term'],
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))
        self.assertEqual(Payment.objects.filter(student=self.fixture['student']).count(), 0)
        self.assertEqual(WalletPayment.objects.count(), 0)

    def test_no_virtual_account_raises(self):
        self.virtual_account.delete()
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'fund-4', 'Funding', 'zainpay_webhook')

        with self.assertRaises(ValueError):
            wallet_service.pay_school_fees_from_wallet(
                self.parent_account, self._fee_selections(), self.fixture['session'], self.fixture['term'],
            )


@override_settings(
    ZAINPAY_TRANSFER_FEE_ESTIMATE=Decimal('300'),
    ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER='9999999999',
)
class PayFeesViewTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.fixture = make_fee_fixture()
        self.parent_account = make_parent_account('view-parent@example.com')
        ParentStudentLink.objects.create(parent_account=self.parent_account, student=self.fixture['student'])
        self.wallet = self.parent_account.wallet
        VirtualAccount.objects.create(
            wallet=self.wallet, account_number='5556667778', account_name='Tunde Okafor', bank_name='zainBank',
        )
        self.client = Client()
        self.client.login(username='view-parent@example.com', password='TestPass123!')

    def test_make_payment_shows_selection_form_not_a_balance_breakdown(self):
        """
        Make Payment is a selection step (pick children + parameters), not a
        balance overview - no fee amounts are computed/shown here at all,
        only on the next page (pay_fees) for whatever was actually selected.
        """
        response = self.client.get('/parent/fees/', {
            'session_id': self.fixture['session'].id, 'term_id': self.fixture['term'].id,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chioma')
        self.assertContains(response, 'New Intake')
        self.assertContains(response, 'Returning')
        self.assertContains(response, 'With Transport')
        self.assertNotContains(response, '50000.00')

    def test_pay_fees_disables_wallet_option_when_balance_insufficient(self):
        response = self.client.get('/parent/fees/pay/', {
            'session_id': self.fixture['session'].id,
            'term_id': self.fixture['term'].id,
            'student_id': self.fixture['student'].id,
            'student_type': 'new', 'transport': 'false',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Insufficient Wallet Balance')

    def test_pay_fees_ignores_student_not_linked_to_parent(self):
        stranger = Student.objects.create(admission_number='GIIA/STRANGER', first_name='X', last_name='Y')
        response = self.client.get('/parent/fees/pay/', {
            'session_id': self.fixture['session'].id,
            'term_id': self.fixture['term'].id,
            'student_id': stranger.id,
            'student_type': 'new', 'transport': 'false',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nothing selected to pay')

    @patch('wallet.services.wallet_service.zainpay_service.transfer_to_school')
    def test_confirm_wallet_payment_end_to_end(self, mock_transfer):
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'view-fund-1', 'Funding', 'zainpay_webhook')
        mock_transfer.return_value = mock_transfer_success(Decimal('50000.00'))

        response = self.client.post('/parent/fees/pay/confirm/', {
            'session_id': self.fixture['session'].id,
            'term_id': self.fixture['term'].id,
            'student_id': [self.fixture['student'].id],
            'student_type': 'new', 'transport': 'false',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Payment Successful')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00') - Decimal('50300.00'))

    def test_receipt_detail_not_visible_to_other_parents(self):
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'view-fund-2', 'Funding', 'zainpay_webhook')

        debit_txn = wallet_service.debit_wallet(
            self.wallet.id, Decimal('500.00'), 'other-parent-debit', 'test debit', 'wallet_fee_payment',
        )
        WalletPayment.objects.create(
            wallet_transaction=debit_txn, session=self.fixture['session'], term=self.fixture['term'],
        )

        make_parent_account('intruder@example.com')
        other_client = Client()
        other_client.login(username='intruder@example.com', password='TestPass123!')

        response = other_client.get(f'/parent/receipts/{debit_txn.reference}/')
        self.assertEqual(response.status_code, 404)


@override_settings(
    ZAINPAY_TRANSFER_FEE_ESTIMATE=Decimal('300'),
    ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER='9999999999',
)
class OtherFeesPaymentTests(TestCase):
    """
    Parity with the existing Paystack unified_payment flow's "Other Fees" tab
    (cardigan, tahfeez, etc), adapted to the wallet's linked-children model.
    """

    def setUp(self):
        seed_site_context_fixtures()
        self.fixture = make_fee_fixture()
        self.parent_account = make_parent_account('other-fees-parent@example.com')
        ParentStudentLink.objects.create(parent_account=self.parent_account, student=self.fixture['student'])

        self.second_student = Student.objects.create(
            admission_number='GIIA/0002', first_name='Femi', last_name='Eze',
            enrolled_class=self.fixture['school_class'],
        )
        ParentStudentLink.objects.create(parent_account=self.parent_account, student=self.second_student)

        self.cardigan = OtherFeeStructure.objects.create(
            name='Cardigan', amount=Decimal('5000.00'),
            session=self.fixture['session'], term=self.fixture['term'], active=True,
        )
        self.tahfeez = OtherFeeStructure.objects.create(
            name='Tahfeez Fee', amount=Decimal('3000.00'),
            session=self.fixture['session'], term=self.fixture['term'], active=True,
        )

        self.wallet = self.parent_account.wallet
        self.virtual_account = VirtualAccount.objects.create(
            wallet=self.wallet, account_number='7778889990', account_name='Tunde Okafor', bank_name='zainBank',
        )
        self.client = Client()
        self.client.login(username='other-fees-parent@example.com', password='TestPass123!')

    def test_make_payment_lists_active_other_fees_and_children(self):
        response = self.client.get('/parent/fees/', {
            'fee_type': 'other_fees',
            'session_id': self.fixture['session'].id, 'term_id': self.fixture['term'].id,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cardigan')
        self.assertContains(response, 'Tahfeez Fee')
        self.assertContains(response, 'Chioma')
        self.assertContains(response, 'Femi')

    def test_pay_fees_builds_one_selection_per_student_per_fee(self):
        response = self.client.get('/parent/fees/pay/', {
            'fee_type': 'other_fees',
            'session_id': self.fixture['session'].id,
            'term_id': self.fixture['term'].id,
            'student_id': [self.fixture['student'].id, self.second_student.id],
            'fee_id': [self.cardigan.id, self.tahfeez.id],
        })
        self.assertEqual(response.status_code, 200)
        # 2 children x 2 fees = 4 line items, total = 2 x (5000 + 3000) = 16000
        self.assertContains(response, '16,000.00')

    @patch('wallet.services.wallet_service.zainpay_service.transfer_to_school')
    def test_confirm_wallet_payment_for_other_fees_creates_correct_payments(self, mock_transfer):
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'other-fees-fund-1', 'Funding', 'zainpay_webhook')
        mock_transfer.return_value = mock_transfer_success(Decimal('16000.00'))

        response = self.client.post('/parent/fees/pay/confirm/', {
            'fee_type': 'other_fees',
            'session_id': self.fixture['session'].id,
            'term_id': self.fixture['term'].id,
            'student_id': [self.fixture['student'].id, self.second_student.id],
            'fee_id': [self.cardigan.id, self.tahfeez.id],
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Payment Successful')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00') - Decimal('16300.00'))

        payments = Payment.objects.filter(payment_method='wallet')
        self.assertEqual(payments.count(), 4)
        self.assertEqual(
            set(payments.values_list('other_fee__name', flat=True)),
            {'Cardigan', 'Tahfeez Fee'},
        )
        self.assertEqual(
            set(payments.values_list('student_id', flat=True)),
            {self.fixture['student'].id, self.second_student.id},
        )

    def test_pay_fees_dedups_student_appearing_in_multiple_fee_selections(self):
        """
        The confirm form only emits each student_id once even though that
        student has multiple fee_selections entries (one per fee) - verified
        by checking the rendered hidden inputs aren't duplicated. Wallet must
        be funded enough for the "Pay from Wallet" form (which holds those
        hidden inputs) to render at all.
        """
        credit_wallet(self.wallet.id, Decimal('100000.00'), 'dedup-fund-1', 'Funding', 'zainpay_webhook')

        response = self.client.get('/parent/fees/pay/', {
            'fee_type': 'other_fees',
            'session_id': self.fixture['session'].id,
            'term_id': self.fixture['term'].id,
            'student_id': [self.fixture['student'].id],
            'fee_id': [self.cardigan.id, self.tahfeez.id],
        })
        self.assertEqual(response.status_code, 200)
        student_id_occurrences = response.content.decode().count(
            f'name="student_id" value="{self.fixture["student"].id}"'
        )
        self.assertEqual(student_id_occurrences, 1)
