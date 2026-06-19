import datetime
import hashlib
import hmac
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from website.models import GeneralInformation, Paragraph, Picture
from wallet.models import ParentAccount, VirtualAccount, WalletTransaction
from wallet.services import zainpay_service
from wallet.services.exceptions import VirtualAccountCreationError, ZainpayTransferError
from wallet.services.wallet_service import credit_wallet


def make_parent_account(email='zain-parent@example.com'):
    user = User.objects.create_user(
        username=email, email=email, password='TestPass123!',
        first_name='Amina', last_name='Bello',
    )
    return ParentAccount.objects.create(
        user=user,
        phone_number='08011112222',
        title='Mrs',
        gender='F',
        date_of_birth=datetime.date(1988, 3, 15),
        bvn='12345678901',
        address='1 Test Street',
        state='Kano',
    )


def seed_site_context_fixtures():
    """
    src.context_processors.data_processor runs on every template render and
    hard-requires specific GeneralInformation/Picture/Paragraph rows to exist
    (pre-existing site behavior, unrelated to the wallet app). The real dev/
    prod database already has this seed data; a fresh test database does not,
    so any test that renders a template needs it created first.
    """
    GeneralInformation.objects.create(
        phone_number='000', email='school@example.com', preamble='x', address='x',
    )
    for title in ['about1', 'choose1', 'car5', 'result-checker', 'grading-system', 'about header bg']:
        Picture.objects.create(title=title, image='pics/placeholder.png')
    Paragraph.objects.create(title='choose_p', content='x')


def mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


@override_settings(
    ZAINPAY_SECRET_KEY='test-secret-key',
    ZAINPAY_ZAINBOX_CODE='TESTBOX123',
    ZAINPAY_BASE_URL='https://sandbox.zainpay.ng',
)
class CreateVirtualAccountTests(TestCase):
    def setUp(self):
        self.parent_account = make_parent_account()

    @patch('wallet.services.zainpay_service.requests.post')
    def test_creates_virtual_account_on_success(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '00',
            'data': {
                'bankName': 'zainBank',
                'email': self.parent_account.user.email,
                'accountName': 'Betastack Test Amina',
                'accountType': '',
                'accountNumber': '2917863937',
            },
            'description': 'successful',
            'status': '200 OK',
        })

        va = zainpay_service.create_virtual_account(self.parent_account)

        self.assertEqual(va.account_number, '2917863937')
        self.assertEqual(va.account_name, 'Betastack Test Amina')
        self.assertEqual(va.wallet, self.parent_account.wallet)

        # Confirm the payload sent matches what Zainpay's docs require.
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['bankType'], 'zainBank')
        self.assertEqual(kwargs['json']['zainboxCode'], 'TESTBOX123')
        self.assertEqual(kwargs['json']['bvn'], '12345678901')

    @patch('wallet.services.zainpay_service.requests.post')
    def test_returns_existing_account_without_calling_zainpay_again(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '00',
            'data': {'bankName': 'zainBank', 'accountName': 'X', 'accountNumber': '111'},
        })
        zainpay_service.create_virtual_account(self.parent_account)
        mock_post.reset_mock()

        va = zainpay_service.create_virtual_account(self.parent_account)

        mock_post.assert_not_called()
        self.assertEqual(va.account_number, '111')

    @patch('wallet.services.zainpay_service.requests.post')
    def test_raises_on_zainpay_error_response(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '04',
            'description': 'Invalid BVN',
        })

        with self.assertRaises(VirtualAccountCreationError):
            zainpay_service.create_virtual_account(self.parent_account)

        self.assertFalse(VirtualAccount.objects.filter(wallet=self.parent_account.wallet).exists())

    @patch('wallet.services.zainpay_service.requests.post', side_effect=requests.exceptions.ConnectionError('timed out'))
    def test_raises_on_network_failure(self, mock_post):
        with self.assertRaises(VirtualAccountCreationError):
            zainpay_service.create_virtual_account(self.parent_account)


@override_settings(ZAINPAY_SECRET_KEY='test-secret-key')
class WebhookSignatureTests(TestCase):
    def test_valid_signature_passes(self):
        body = b'{"data": {"txnRef": "abc123"}}'
        signature = hmac.new(b'test-secret-key', body, hashlib.sha256).hexdigest()
        self.assertTrue(zainpay_service.verify_webhook_signature(body, signature))

    def test_invalid_signature_fails(self):
        body = b'{"data": {"txnRef": "abc123"}}'
        self.assertFalse(zainpay_service.verify_webhook_signature(body, 'not-the-right-signature'))

    def test_missing_signature_fails(self):
        body = b'{"data": {"txnRef": "abc123"}}'
        self.assertFalse(zainpay_service.verify_webhook_signature(body, None))
        self.assertFalse(zainpay_service.verify_webhook_signature(body, ''))


@override_settings(
    ZAINPAY_SECRET_KEY='test-secret-key',
    ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER='9999999999',
    ZAINPAY_SCHOOL_SETTLEMENT_BANK_CODE='000001',
    ZAINPAY_BANK_CODE='000099',
    ZAINPAY_ZAINBOX_CODE='TESTBOX123',
    SITE_BASE_URL='http://localhost:8000',
)
class TransferToSchoolTests(TestCase):
    @patch('wallet.services.zainpay_service.requests.post')
    def test_successful_transfer_returns_data(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '200 OK',
            'data': {
                'amount': '5000',
                'status': 'success',
                'totalTxnAmount': '5300',
                'txnFee': '300',
                'txnRef': 'fee-pay-ref-1',
            },
            'description': 'Funds Transfer Successful',
        })

        result = zainpay_service.transfer_to_school('2917863937', 5000, 'fee-pay-ref-1', 'School fees')

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['totalTxnAmount'], '5300')

    @patch('wallet.services.zainpay_service.requests.post')
    def test_failed_transfer_raises(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '500 Bad gateway',
            'data': {
                'status': 'failed',
                'failureReason': 'destination bank not responding',
            },
            'description': 'Funds Transfer Failed!',
        })

        with self.assertRaises(ZainpayTransferError):
            zainpay_service.transfer_to_school('2917863937', 5000, 'fee-pay-ref-2', 'School fees')


@override_settings(ZAINPAY_SECRET_KEY='test-secret-key')
class DepositWebhookViewTests(TestCase):
    def setUp(self):
        self.parent_account = make_parent_account('webhook-parent@example.com')
        self.wallet = self.parent_account.wallet
        self.virtual_account = VirtualAccount.objects.create(
            wallet=self.wallet,
            account_number='2917863937',
            account_name='Amina Bello',
            bank_name='zainBank',
        )
        self.client = Client()

    def _post_webhook(self, body_dict, signature=None):
        body = json.dumps(body_dict).encode('utf-8')
        if signature is None:
            signature = hmac.new(b'test-secret-key', body, hashlib.sha256).hexdigest()
        return self.client.post(
            '/parent/webhooks/zainpay/',
            data=body,
            content_type='application/json',
            HTTP_ZAINPAY_SIGNATURE=signature,
        )

    @patch('wallet.webhooks.zainpay_service.verify_deposit')
    def test_credits_wallet_with_full_deposited_amount_in_naira(self, mock_verify):
        """
        Zainpay promised zero settlement charges on deposits, so the wallet
        must be credited the full depositedAmount from the webhook payload -
        not verify_deposit's amountAfterCharges, which would include any
        charge. Amounts are in kobo (confirmed in production: a real ₦100
        deposit arrived as raw 10000) and must be converted to naira.
        """
        mock_verify.return_value = {
            'amountAfterCharges': 4950,  # would be wrong to use - implies a charge was deducted
            'beneficiaryAccountNumber': '2917863937',
            'txnRef': 'dep-ref-1',
        }

        response = self._post_webhook({
            'event': 'deposit.success',
            'data': {
                'txnRef': 'dep-ref-1',
                'depositedAmount': '5000',  # kobo - a real ₦50 deposit
                'txnChargesAmount': '50',
                'amountAfterCharges': '4950',
            },
        })

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50.00'))
        self.assertTrue(WalletTransaction.objects.filter(reference='dep-ref-1').exists())

    @patch('wallet.webhooks.zainpay_service.verify_deposit')
    def test_falls_back_to_amount_after_charges_when_deposited_amount_missing(self, mock_verify):
        mock_verify.return_value = {
            'amountAfterCharges': 2000,  # kobo
            'beneficiaryAccountNumber': '2917863937',
            'txnRef': 'dep-ref-fallback',
        }

        response = self._post_webhook({'event': 'deposit.success', 'data': {'txnRef': 'dep-ref-fallback'}})

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('20.00'))

    def test_rejects_invalid_signature(self):
        response = self._post_webhook(
            {'event': 'deposit.success', 'data': {'txnRef': 'dep-ref-bad-sig'}}, signature='wrong-signature',
        )
        self.assertEqual(response.status_code, 401)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    @patch('wallet.webhooks.zainpay_service.verify_deposit')
    def test_duplicate_webhook_delivery_does_not_double_credit(self, mock_verify):
        mock_verify.return_value = {
            'amountAfterCharges': 3000,
            'beneficiaryAccountNumber': '2917863937',
            'txnRef': 'dep-ref-2',
        }
        body = {
            'event': 'deposit.success',
            'data': {'txnRef': 'dep-ref-2', 'depositedAmount': '3000'},
        }

        self._post_webhook(body)
        response2 = self._post_webhook(body)

        self.assertEqual(response2.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('30.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='dep-ref-2').count(), 1)

    @patch('wallet.webhooks.zainpay_service.verify_deposit')
    def test_unverifiable_deposit_is_ignored_not_credited(self, mock_verify):
        mock_verify.return_value = None

        response = self._post_webhook({'event': 'deposit.success', 'data': {'txnRef': 'dep-ref-unverifiable'}})

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    @patch('wallet.webhooks.zainpay_service.verify_deposit')
    def test_unknown_account_number_is_ignored(self, mock_verify):
        mock_verify.return_value = {
            'amountAfterCharges': 1000,
            'beneficiaryAccountNumber': 'NOT-A-REAL-ACCOUNT',
            'txnRef': 'dep-ref-unknown-acct',
        }

        response = self._post_webhook({'event': 'deposit.success', 'data': {'txnRef': 'dep-ref-unknown-acct'}})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WalletTransaction.objects.filter(reference='dep-ref-unknown-acct').exists())

    @patch('wallet.webhooks.zainpay_service.verify_deposit')
    def test_transfer_events_are_ignored_not_processed_as_deposits(self, mock_verify):
        """
        Zainpay sends transfer.success/transfer.failed to this same callback
        URL (e.g. when pay_school_fees_from_wallet's outgoing transfer
        completes) - these must never be mistaken for a deposit.
        """
        response = self._post_webhook({
            'event': 'transfer.success',
            'data': {'txnRef': 'transfer-ref-1', 'accountNumber': '2917863937'},
        })

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))


class WalletActivationViewTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.parent_account = make_parent_account('activate-parent@example.com')
        self.client = Client()
        self.client.login(username='activate-parent@example.com', password='TestPass123!')

    @patch('wallet.views.zainpay_service.create_virtual_account')
    def test_activate_creates_virtual_account_and_redirects(self, mock_create):
        mock_create.return_value = VirtualAccount(
            wallet=self.parent_account.wallet,
            account_number='123456',
            account_name='Test',
            bank_name='zainBank',
        )

        response = self.client.post('/parent/wallet/activate/', follow=True)

        self.assertEqual(response.status_code, 200)
        mock_create.assert_called_once_with(self.parent_account)

    @patch('wallet.views.zainpay_service.create_virtual_account', side_effect=VirtualAccountCreationError('boom'))
    def test_activation_failure_shows_message_not_crash(self, mock_create):
        response = self.client.post('/parent/wallet/activate/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Could not set up your funding account')

    def test_get_method_not_allowed(self):
        response = self.client.get('/parent/wallet/activate/')
        self.assertEqual(response.status_code, 405)


class ReconciliationCommandTests(TestCase):
    def test_reconcile_detects_balance_mismatch(self):
        parent_account = make_parent_account('reconcile-parent@example.com')
        wallet = parent_account.wallet
        credit_wallet(wallet.id, Decimal('1000.00'), 'recon-ref-1', 'Funding', 'zainpay_webhook')

        # Simulate drift: directly corrupt the cached balance, bypassing the service.
        wallet.balance = Decimal('9999.00')
        wallet.save(update_fields=['balance'])

        out = StringIO()
        call_command('reconcile_wallet_balances', stdout=out)
        output = out.getvalue()

        self.assertIn('MISMATCH', output)
        self.assertIn(str(wallet.id), output)

    def test_reconcile_reports_clean_when_balances_match(self):
        parent_account = make_parent_account('clean-parent@example.com')
        credit_wallet(parent_account.wallet.id, Decimal('500.00'), 'recon-ref-2', 'Funding', 'zainpay_webhook')

        out = StringIO()
        call_command('reconcile_wallet_balances', stdout=out)
        output = out.getvalue()

        self.assertNotIn('MISMATCH', output)
        self.assertIn('all balances match', output)


class SyncZainpayTransactionsCommandTests(TestCase):
    @patch('wallet.management.commands.sync_zainpay_transactions.zainpay_service.list_account_transactions')
    def test_credits_missed_deposit(self, mock_list):
        parent_account = make_parent_account('sync-parent@example.com')
        virtual_account = VirtualAccount.objects.create(
            wallet=parent_account.wallet,
            account_number='5551234567',
            account_name='Sync Test',
            bank_name='zainBank',
        )
        mock_list.return_value = [
            {
                'transactionType': 'deposit',
                'transactionRef': 'missed-deposit-1',
                'amount': 50000,  # kobo - a real ₦500 deposit
            }
        ]

        out = StringIO()
        call_command('sync_zainpay_transactions', stdout=out)

        parent_account.wallet.refresh_from_db()
        # Regression test: a real production deposit of ₦500 showed up here as
        # raw amount 50000 (kobo) and was credited as ₦50,000 before this fix.
        self.assertEqual(parent_account.wallet.balance, Decimal('500.00'))
        self.assertTrue(WalletTransaction.objects.filter(reference='missed-deposit-1').exists())
        self.assertIn('Credited missed deposit', out.getvalue())

    @patch('wallet.management.commands.sync_zainpay_transactions.zainpay_service.list_account_transactions')
    def test_does_not_recredit_known_transaction(self, mock_list):
        parent_account = make_parent_account('sync-parent-2@example.com')
        virtual_account = VirtualAccount.objects.create(
            wallet=parent_account.wallet,
            account_number='5551234999',
            account_name='Sync Test 2',
            bank_name='zainBank',
        )
        credit_wallet(parent_account.wallet.id, Decimal('1000.00'), 'already-known-ref', 'Funding', 'zainpay_webhook')

        mock_list.return_value = [
            {'transactionType': 'deposit', 'transactionRef': 'already-known-ref', 'amount': 1000},
        ]

        out = StringIO()
        call_command('sync_zainpay_transactions', stdout=out)

        parent_account.wallet.refresh_from_db()
        self.assertEqual(parent_account.wallet.balance, Decimal('1000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='already-known-ref').count(), 1)
