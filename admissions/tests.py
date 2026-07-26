import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from src.models import Section, SchoolClass, Session

from .models import AdmissionOpening, Applicant
from .services import total_with_zainpay_charge


def make_session():
    return Session.objects.create(
        name='2026/2027', start_date='2026-09-01', end_date='2027-07-31', current=True,
    )


def make_school_class(name='KINDERGARTEN BOYS', section_name='KINDERGARTEN'):
    section, _ = Section.objects.get_or_create(name=section_name)
    return SchoolClass.objects.create(name=name, level='Nil', arm='A', section=section)


def make_applicant(session, school_class, reference='APPFEE-TEST1', paid=False):
    return Applicant.objects.create(
        first_name='Amina', last_name='Bello', date_of_birth='2020-01-01', gender='Female',
        residential_address='1 Test Street', father_name='Bello Musa', father_phone='08011112222',
        session=session, desired_class=school_class, declaration_name='Bello Musa', declaration_agreed=True,
        reference=reference, application_fee_paid=paid,
    )


class ZainpayChargeTests(TestCase):
    def test_grosses_up_so_school_still_nets_the_base_fee(self):
        # (5000 + 50) / (1 - 0.015) = 5126.90 - after Zainpay deducts
        # 1.5% + 50 from that on settlement, the school nets ~5000.
        gross = total_with_zainpay_charge(Decimal('5000.00'))
        self.assertEqual(gross, Decimal('5126.90'))
        net = gross - (gross * Decimal('0.015') + Decimal('50'))
        self.assertAlmostEqual(net, Decimal('5000.00'), delta=Decimal('0.01'))

    def test_rounds_to_2_decimal_places(self):
        self.assertEqual(total_with_zainpay_charge(Decimal('999')), Decimal('1064.97'))


class AdmissionOpeningTests(TestCase):
    def setUp(self):
        self.session = make_session()
        self.school_class = make_school_class()

    def test_unlimited_when_capacity_none(self):
        opening = AdmissionOpening.objects.create(session=self.session, school_class=self.school_class, capacity=None)
        for i in range(5):
            make_applicant(self.session, self.school_class, reference=f'APPFEE-{i}', paid=True)
        self.assertFalse(opening.is_full)
        self.assertTrue(opening.is_available)

    def test_only_paid_applicants_count_toward_capacity(self):
        opening = AdmissionOpening.objects.create(session=self.session, school_class=self.school_class, capacity=2)
        make_applicant(self.session, self.school_class, reference='APPFEE-1', paid=False)
        make_applicant(self.session, self.school_class, reference='APPFEE-2', paid=False)
        self.assertEqual(opening.paid_applicant_count, 0)
        self.assertFalse(opening.is_full)

    def test_closes_once_capacity_reached(self):
        opening = AdmissionOpening.objects.create(session=self.session, school_class=self.school_class, capacity=2)
        make_applicant(self.session, self.school_class, reference='APPFEE-1', paid=True)
        make_applicant(self.session, self.school_class, reference='APPFEE-2', paid=True)
        self.assertTrue(opening.is_full)
        self.assertFalse(opening.is_available)

    def test_manual_is_open_switch_closes_regardless_of_capacity(self):
        opening = AdmissionOpening.objects.create(
            session=self.session, school_class=self.school_class, capacity=None, is_open=False,
        )
        self.assertFalse(opening.is_available)


class ApplyViewTests(TestCase):
    def setUp(self):
        from wallet.test_support import seed_site_context_fixtures
        seed_site_context_fixtures()
        self.session = make_session()
        self.open_class = make_school_class('KINDERGARTEN BOYS', 'KINDERGARTEN')
        self.full_class = make_school_class('KINDERGARTEN GIRLS', 'KINDERGARTEN')
        AdmissionOpening.objects.create(session=self.session, school_class=self.open_class, capacity=30)
        AdmissionOpening.objects.create(session=self.session, school_class=self.full_class, capacity=1)
        make_applicant(self.session, self.full_class, reference='APPFEE-FULL1', paid=True)
        self.client = Client()

    def test_only_open_not_full_classes_listed(self):
        response = self.client.get('/apply/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KINDERGARTEN BOYS')
        self.assertNotContains(response, 'KINDERGARTEN GIRLS')

    def _valid_payload(self, desired_class_id):
        return {
            'desired_class': desired_class_id,
            'first_name': 'Test', 'last_name': 'Applicant', 'gender': 'Male',
            'date_of_birth': '2020-01-01', 'residential_address': 'addr',
            'father_name': 'Father Name', 'father_phone': '08011112222',
            'declaration_name': 'Father Name', 'declaration_agreed': 'on',
        }

    @patch('admissions.views.zainpay_service.initialize_checkout')
    def test_submitting_for_full_class_is_rejected(self, mock_initialize):
        response = self.client.post('/apply/', self._valid_payload(self.full_class.id), follow=True)

        self.assertEqual(response.status_code, 200)
        mock_initialize.assert_not_called()
        self.assertEqual(Applicant.objects.filter(desired_class=self.full_class).count(), 1)

    @patch('admissions.views.zainpay_service.initialize_checkout')
    def test_valid_submission_creates_applicant_and_redirects_to_checkout(self, mock_initialize):
        mock_initialize.return_value = 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc'

        response = self.client.post('/apply/', self._valid_payload(self.open_class.id))

        self.assertRedirects(response, 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc', fetch_redirect_response=False)
        applicant = Applicant.objects.get(first_name='Test')
        self.assertEqual(applicant.desired_class, self.open_class)
        self.assertFalse(applicant.application_fee_paid)
        mock_initialize.assert_called_once()
        self.assertEqual(
            mock_initialize.call_args.kwargs['amount'],
            total_with_zainpay_charge(Decimal('5000.00')),
        )

    @patch('admissions.views.zainpay_service.initialize_checkout')
    def test_missing_declaration_agreement_is_rejected(self, mock_initialize):
        payload = self._valid_payload(self.open_class.id)
        del payload['declaration_agreed']

        response = self.client.post('/apply/', payload, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_initialize.assert_not_called()
        self.assertFalse(Applicant.objects.filter(first_name='Test').exists())


class ApplyCallbackTests(TestCase):
    def setUp(self):
        from wallet.test_support import seed_site_context_fixtures
        seed_site_context_fixtures()
        self.session = make_session()
        self.school_class = make_school_class()
        self.client = Client()

    @patch('admissions.services.zainpay_service.list_account_transactions')
    def test_credits_on_confirmed_deposit(self, mock_list):
        applicant = make_applicant(self.session, self.school_class, reference='APPFEE-CB1', paid=False)
        mock_list.return_value = [
            {'transactionType': 'deposit', 'transactionRef': 'APPFEE-CB1', 'amount': 500000},
        ]

        response = self.client.get('/apply/callback/?status=success&txnRef=APPFEE-CB1', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, '/apply/receipt/APPFEE-CB1/')
        applicant.refresh_from_db()
        self.assertTrue(applicant.application_fee_paid)
        self.assertEqual(applicant.amount_paid, Decimal('5000.00'))

    @patch('admissions.services.zainpay_service.list_account_transactions')
    def test_pending_message_when_not_yet_confirmed(self, mock_list):
        applicant = make_applicant(self.session, self.school_class, reference='APPFEE-CB2', paid=False)
        mock_list.return_value = []

        response = self.client.get('/apply/callback/?status=success&txnRef=APPFEE-CB2', follow=True)

        self.assertEqual(response.status_code, 200)
        applicant.refresh_from_db()
        self.assertFalse(applicant.application_fee_paid)

    def test_cancelled_status_never_checks_zainpay(self):
        with patch('admissions.services.zainpay_service.list_account_transactions') as mock_list:
            response = self.client.get('/apply/callback/?status=cancel&txnRef=APPFEE-CB3', follow=True)
            self.assertEqual(response.status_code, 200)
            mock_list.assert_not_called()


@override_settings(ZAINPAY_SECRET_KEY='test-secret-key', ZAINPAY_SCHOOL_ZAINBOX_CODE='89400_HSsbCKey2Luz8jaqhqOH')
class SchoolZainboxWebhookRoutingTests(TestCase):
    """
    The wallet app's zainpay_deposit_webhook is the single callback URL
    registered for both Zainboxes - confirm it correctly routes a
    school-Zainbox deposit through to admissions.services when it matches an
    Applicant reference, without needing any admissions-specific webhook URL.
    """
    def setUp(self):
        self.session = make_session()
        self.school_class = make_school_class()
        self.client = Client()

    def _post_webhook(self, body_dict):
        body = json.dumps(body_dict).encode('utf-8')
        signature = hmac.new(b'test-secret-key', body, hashlib.sha256).hexdigest()
        return self.client.post(
            '/parent/webhooks/zainpay/', data=body, content_type='application/json',
            HTTP_ZAINPAY_SIGNATURE=signature,
        )

    @patch('admissions.services.zainpay_service.list_account_transactions')
    def test_school_zainbox_deposit_confirms_matching_applicant(self, mock_list):
        applicant = make_applicant(self.session, self.school_class, reference='APPFEE-WH1', paid=False)
        mock_list.return_value = [
            {'transactionType': 'deposit', 'transactionRef': 'APPFEE-WH1', 'amount': 500000},
        ]

        response = self._post_webhook({
            'event': 'deposit.success',
            'data': {'txnRef': 'APPFEE-WH1', 'zainboxCode': '89400_HSsbCKey2Luz8jaqhqOH'},
        })

        self.assertEqual(response.status_code, 200)
        applicant.refresh_from_db()
        self.assertTrue(applicant.application_fee_paid)

    @patch('admissions.services.zainpay_service.list_account_transactions')
    def test_school_zainbox_deposit_with_no_matching_applicant_is_ignored(self, mock_list):
        response = self._post_webhook({
            'event': 'deposit.success',
            'data': {'txnRef': 'no-such-application', 'zainboxCode': '89400_HSsbCKey2Luz8jaqhqOH'},
        })

        self.assertEqual(response.status_code, 200)
        mock_list.assert_not_called()


class ApplicationReceiptViewTests(TestCase):
    def setUp(self):
        from wallet.test_support import seed_site_context_fixtures
        seed_site_context_fixtures()
        self.session = make_session()
        self.school_class = make_school_class()
        self.client = Client()

    def test_renders_for_paid_applicant(self):
        applicant = make_applicant(self.session, self.school_class, reference='APPFEE-RCPT1', paid=True)
        applicant.amount_paid = Decimal('5000.00')
        applicant.save(update_fields=['amount_paid'])

        response = self.client.get('/apply/receipt/APPFEE-RCPT1/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, applicant.app_number)
        self.assertContains(response, 'Amina')
        self.assertContains(response, 'Bello Musa')

    def test_404_for_unpaid_applicant(self):
        make_applicant(self.session, self.school_class, reference='APPFEE-RCPT2', paid=False)

        response = self.client.get('/apply/receipt/APPFEE-RCPT2/')

        self.assertEqual(response.status_code, 404)

    def test_404_for_unknown_reference(self):
        response = self.client.get('/apply/receipt/does-not-exist/')

        self.assertEqual(response.status_code, 404)
