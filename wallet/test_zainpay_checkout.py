import hashlib
import hmac
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from wallet.models import ParentAccount, WalletFundingRequest, WalletTransaction
from wallet.services import zainpay_service
from wallet.services.exceptions import ZainpayCheckoutError, ZainpayTransferError
from wallet.services.wallet_service import credit_wallet


def make_parent_account(email='zain-parent@example.com'):
    user = User.objects.create_user(
        username=email, email=email, password='TestPass123!',
        first_name='Amina', last_name='Bello',
    )
    return ParentAccount.objects.create(user=user, phone_number='08011112222')


def mock_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    return resp


@override_settings(
    ZAINPAY_BASE_URL='https://sandbox.zainpay.ng',
    ZAINPAY_WALLET_ZAINBOX_CODE='76812_yJ8B7wyLV38ypP2Noqgc',
)
class InitializeCheckoutTests(TestCase):
    def setUp(self):
        self.parent_account = make_parent_account()

    @patch('wallet.services.zainpay_service.requests.post')
    def test_returns_redirect_url_on_success(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '00',
            'data': 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc123',
            'description': 'card processing initialization',
            'status': '200 OK',
        })

        url = zainpay_service.initialize_checkout(
            email=self.parent_account.user.email,
            mobile_number=self.parent_account.phone_number,
            amount=Decimal('5000.00'),
            txn_ref='WALLETFUND-1',
            callback_url='https://example.com/callback/',
            zainbox_code='76812_yJ8B7wyLV38ypP2Noqgc',
        )

        self.assertEqual(url, 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc123')

        _, kwargs = mock_post.call_args
        sent = kwargs['json']
        # Whole-naira amounts are sent without a decimal suffix (Zainpay
        # rejected both a JSON float and a "5000.00" string as invalid).
        self.assertEqual(sent['amount'], '5000')
        self.assertIsInstance(sent['amount'], str)
        self.assertEqual(sent['txnRef'], 'WALLETFUND-1')
        self.assertEqual(sent['zainboxCode'], '76812_yJ8B7wyLV38ypP2Noqgc')
        self.assertEqual(sent['mobileNumber'], '08011112222')
        self.assertEqual(sent['callBackUrl'], 'https://example.com/callback/')
        self.assertEqual(sent['paymentChannels'], ['bank_transfer'])

    @patch('wallet.services.zainpay_service.requests.post')
    def test_fractional_amount_keeps_decimal_suffix(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '00',
            'data': 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc456',
        })

        zainpay_service.initialize_checkout(
            email=self.parent_account.user.email,
            mobile_number=self.parent_account.phone_number,
            amount=Decimal('767.75'),
            txn_ref='WALLETFUND-1B',
            callback_url='https://example.com/callback/',
            zainbox_code='76812_yJ8B7wyLV38ypP2Noqgc',
        )

        sent = mock_post.call_args.kwargs['json']
        self.assertEqual(sent['amount'], '767.75')

    @patch('wallet.services.zainpay_service.requests.post')
    def test_raises_on_zainpay_error_response(self, mock_post):
        mock_post.return_value = mock_response({'code': '04', 'description': 'Invalid mobile number'})

        with self.assertRaises(ZainpayCheckoutError):
            zainpay_service.initialize_checkout(
                email=self.parent_account.user.email,
                mobile_number=self.parent_account.phone_number,
                amount=Decimal('5000.00'),
                txn_ref='WALLETFUND-2',
                callback_url='https://example.com/callback/',
                zainbox_code='76812_yJ8B7wyLV38ypP2Noqgc',
            )

    @patch('wallet.services.zainpay_service.requests.post', side_effect=requests.exceptions.ConnectionError('timed out'))
    def test_raises_on_network_failure(self, mock_post):
        with self.assertRaises(ZainpayCheckoutError):
            zainpay_service.initialize_checkout(
                email=self.parent_account.user.email,
                mobile_number=self.parent_account.phone_number,
                amount=Decimal('5000.00'),
                txn_ref='WALLETFUND-3',
                callback_url='https://example.com/callback/',
                zainbox_code='76812_yJ8B7wyLV38ypP2Noqgc',
            )


@override_settings(
    ZAINPAY_BASE_URL='https://sandbox.zainpay.ng',
    ZAINPAY_WALLET_ZAINBOX_CODE='76812_yJ8B7wyLV38ypP2Noqgc',
    ZAINPAY_WALLET_ACCOUNT_NUMBER='4812833397',
    ZAINPAY_WALLET_BANK_CODE='090976',
    ZAINPAY_SCHOOL_SETTLEMENT_ACCOUNT_NUMBER='4812350098',
    ZAINPAY_SCHOOL_SETTLEMENT_BANK_CODE='090976',
    SITE_BASE_URL='http://localhost:8000',
)
class TransferWalletToSchoolTests(TestCase):
    @patch('wallet.services.zainpay_service.requests.post')
    def test_successful_transfer_uses_fixed_wallet_and_school_accounts(self, mock_post):
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

        result = zainpay_service.transfer_wallet_to_school(Decimal('5000.00'), 'fee-pay-ref-1', 'School fees')

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['totalTxnAmount'], '5300')

        _, kwargs = mock_post.call_args
        sent = kwargs['json']
        self.assertEqual(sent['sourceAccountNumber'], '4812833397')
        self.assertEqual(sent['sourceBankCode'], '090976')
        self.assertEqual(sent['destinationAccountNumber'], '4812350098')
        self.assertEqual(sent['destinationBankCode'], '090976')
        self.assertEqual(sent['zainboxCode'], '76812_yJ8B7wyLV38ypP2Noqgc')

    @patch('wallet.services.zainpay_service.requests.post')
    def test_failed_transfer_raises(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '500 Bad gateway',
            'data': {'status': 'failed', 'failureReason': 'destination bank not responding'},
            'description': 'Funds Transfer Failed!',
        })

        with self.assertRaises(ZainpayTransferError):
            zainpay_service.transfer_wallet_to_school(Decimal('5000.00'), 'fee-pay-ref-2', 'School fees')

    @patch('wallet.services.zainpay_service.requests.post')
    def test_amount_sent_as_kobo_integer(self, mock_post):
        mock_post.return_value = mock_response({
            'code': '200 OK',
            'data': {'amount': '1200000', 'status': 'success', 'totalTxnAmount': '1230000', 'txnFee': '30000', 'txnRef': 'fee-pay-ref-3'},
            'description': 'Funds Transfer Successful',
        })

        zainpay_service.transfer_wallet_to_school(Decimal('12000.00'), 'fee-pay-ref-3', 'School fees')

        sent_payload = mock_post.call_args.kwargs['json']
        self.assertEqual(sent_payload['amount'], '1200000')


@override_settings(WALLET_FUNDING_PROVIDER='zainpay')
class WalletFundInitiateZainpayTests(TestCase):
    def setUp(self):
        from wallet.test_support import seed_site_context_fixtures
        seed_site_context_fixtures()
        self.parent_account = make_parent_account('initiate-parent@example.com')
        self.wallet = self.parent_account.wallet
        self.client = Client()
        self.client.login(username='initiate-parent@example.com', password='TestPass123!')

    @patch('wallet.services.zainpay_service.initialize_checkout')
    def test_creates_funding_request_and_redirects(self, mock_initialize):
        mock_initialize.return_value = 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc'

        response = self.client.post('/parent/wallet/fund/', {'amount': '5000.00'})

        self.assertRedirects(response, 'https://dev.zainpay.ng/merchant/redirect-payment?e=abc', fetch_redirect_response=False)
        funding_request = WalletFundingRequest.objects.get(wallet=self.wallet)
        self.assertEqual(funding_request.amount, Decimal('5000.00'))
        mock_initialize.assert_called_once()
        call_kwargs = mock_initialize.call_args.kwargs
        self.assertEqual(call_kwargs['email'], self.parent_account.user.email)
        self.assertEqual(call_kwargs['mobile_number'], self.parent_account.phone_number)
        self.assertEqual(call_kwargs['amount'], Decimal('5000.00'))
        self.assertEqual(call_kwargs['txn_ref'], funding_request.reference)
        self.assertEqual(call_kwargs['zainbox_code'], settings.ZAINPAY_WALLET_ZAINBOX_CODE)

    @patch('wallet.services.zainpay_service.initialize_checkout')
    def test_zero_amount_never_calls_zainpay(self, mock_initialize):
        response = self.client.post('/parent/wallet/fund/', {'amount': '0'}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_initialize.assert_not_called()
        self.assertFalse(WalletFundingRequest.objects.exists())

    @patch('wallet.services.zainpay_service.initialize_checkout')
    def test_inactive_wallet_never_calls_zainpay(self, mock_initialize):
        self.wallet.is_active = False
        self.wallet.save(update_fields=['is_active'])

        response = self.client.post('/parent/wallet/fund/', {'amount': '5000.00'}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_initialize.assert_not_called()

    @patch('wallet.services.zainpay_service.initialize_checkout', side_effect=ZainpayCheckoutError('boom'))
    def test_checkout_error_shows_message_and_redirects(self, mock_initialize):
        response = self.client.post('/parent/wallet/fund/', {'amount': '5000.00'}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Could not start wallet funding')


@override_settings(WALLET_FUNDING_PROVIDER='zainpay', ZAINPAY_WALLET_ACCOUNT_NUMBER='4812833397')
class WalletFundCallbackZainpayTests(TestCase):
    """
    Production incident: the browser redirect back from Zainpay's checkout
    page appends ?status=...&txnRef=... query params (confirmed live), and
    the deposit.success webhook isn't reliably arriving - so the callback
    actively verifies against Zainpay's own transaction history instead of
    just showing a generic message.
    """
    def setUp(self):
        from wallet.test_support import seed_site_context_fixtures
        seed_site_context_fixtures()
        self.parent_account = make_parent_account('callback-zp-parent@example.com')
        self.wallet = self.parent_account.wallet
        self.client = Client()
        self.client.login(username='callback-zp-parent@example.com', password='TestPass123!')

    @patch('wallet.services.zainpay_service.list_account_transactions')
    def test_credits_wallet_when_deposit_is_confirmed(self, mock_list):
        funding_request = WalletFundingRequest.objects.create(
            wallet=self.wallet, reference='WALLETFUND-CB1', amount=Decimal('500.00'),
        )
        mock_list.return_value = [
            {'transactionType': 'deposit', 'transactionRef': 'WALLETFUND-CB1', 'amount': 49250},
        ]

        response = self.client.get('/parent/wallet/fund/callback/?status=success&txnRef=WALLETFUND-CB1', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'funded successfully')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('492.50'))

    @patch('wallet.services.zainpay_service.list_account_transactions')
    def test_shows_pending_message_when_deposit_not_yet_confirmed(self, mock_list):
        WalletFundingRequest.objects.create(
            wallet=self.wallet, reference='WALLETFUND-CB2', amount=Decimal('500.00'),
        )
        mock_list.return_value = []

        response = self.client.get('/parent/wallet/fund/callback/?status=success&txnRef=WALLETFUND-CB2', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'update within a few minutes')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_cancelled_status_shows_error_and_never_calls_zainpay(self):
        with patch('wallet.services.zainpay_service.list_account_transactions') as mock_list:
            response = self.client.get('/parent/wallet/fund/callback/?status=cancel&txnRef=WALLETFUND-CB3', follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'not completed')
            mock_list.assert_not_called()

    def test_missing_txn_ref_shows_generic_pending_message(self):
        response = self.client.get('/parent/wallet/fund/callback/', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'update within a few minutes')

    def test_unauthenticated_post_does_not_error(self):
        # Simulates a server-to-server hit with no browser session at all -
        # must not redirect to login or raise, just be logged and ignored.
        anon_client = Client()
        response = anon_client.post('/parent/wallet/fund/callback/', content_type='application/json', data='{}')

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('/parent/login/', response.url)


@override_settings(
    ZAINPAY_SECRET_KEY='test-secret-key',
    ZAINPAY_WALLET_ZAINBOX_CODE='76812_yJ8B7wyLV38ypP2Noqgc',
)
class ZainpayDepositWebhookTests(TestCase):
    def setUp(self):
        self.parent_account = make_parent_account('webhook-parent@example.com')
        self.wallet = self.parent_account.wallet
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

    def test_credits_wallet_for_deposit_on_wallet_zainbox(self):
        funding_request = WalletFundingRequest.objects.create(
            wallet=self.wallet, reference='dep-ref-1', amount=Decimal('50.00'),
        )

        response = self._post_webhook({
            'event': 'deposit.success',
            'data': {
                'txnRef': funding_request.reference,
                'depositedAmount': '5000',  # kobo - a real ₦50 deposit
                'zainboxCode': '76812_yJ8B7wyLV38ypP2Noqgc',
            },
        })

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50.00'))
        self.assertTrue(WalletTransaction.objects.filter(reference='dep-ref-1').exists())

    def test_ignores_deposit_on_school_zainbox(self):
        response = self._post_webhook({
            'event': 'deposit.success',
            'data': {
                'txnRef': 'school-settlement-ref',
                'depositedAmount': '5300',
                'zainboxCode': '89400_HSsbCKey2Luz8jaqhqOH',
            },
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WalletTransaction.objects.filter(reference='school-settlement-ref').exists())

    def test_ignores_transfer_events(self):
        response = self._post_webhook({
            'event': 'transfer.success',
            'data': {'txnRef': 'transfer-ref-1', 'zainboxCode': '76812_yJ8B7wyLV38ypP2Noqgc'},
        })

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_duplicate_webhook_delivery_does_not_double_credit(self):
        funding_request = WalletFundingRequest.objects.create(
            wallet=self.wallet, reference='dep-ref-2', amount=Decimal('30.00'),
        )
        body = {
            'event': 'deposit.success',
            'data': {
                'txnRef': funding_request.reference,
                'depositedAmount': '3000',
                'zainboxCode': '76812_yJ8B7wyLV38ypP2Noqgc',
            },
        }

        self._post_webhook(body)
        response2 = self._post_webhook(body)

        self.assertEqual(response2.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('30.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='dep-ref-2').count(), 1)

    def test_rejects_invalid_signature(self):
        response = self._post_webhook(
            {'event': 'deposit.success', 'data': {'txnRef': 'dep-ref-bad-sig'}}, signature='wrong-signature',
        )
        self.assertEqual(response.status_code, 401)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_no_matching_funding_request_is_ignored_not_credited(self):
        response = self._post_webhook({
            'event': 'deposit.success',
            'data': {
                'txnRef': 'unknown-ref',
                'depositedAmount': '1000',
                'zainboxCode': '76812_yJ8B7wyLV38ypP2Noqgc',
            },
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WalletTransaction.objects.filter(reference='unknown-ref').exists())


@override_settings(ZAINPAY_WALLET_ACCOUNT_NUMBER='4812833397')
class SyncZainpayTransactionsCommandTests(TestCase):
    """
    Safety net for exactly the production incident this was built for: a
    deposit lands on Zainpay's side (confirmed via list_account_transactions)
    but the deposit.success webhook never arrives, so the wallet is never
    credited without this command catching it.
    """
    @patch('wallet.management.commands.sync_zainpay_transactions.zainpay_service.list_account_transactions')
    def test_credits_missed_deposit(self, mock_list):
        parent_account = make_parent_account('sync-parent@example.com')
        funding_request = WalletFundingRequest.objects.create(
            wallet=parent_account.wallet, reference='WALLETFUND-MISSED1', amount=Decimal('500.00'),
        )
        mock_list.return_value = [
            {
                'transactionType': 'deposit',
                'transactionRef': 'WALLETFUND-MISSED1',
                'amount': 49250,  # kobo - Zainpay's own fee already deducted
            },
        ]

        out = StringIO()
        call_command('sync_zainpay_transactions', stdout=out)

        funding_request.wallet.refresh_from_db()
        self.assertEqual(funding_request.wallet.balance, Decimal('492.50'))
        self.assertTrue(WalletTransaction.objects.filter(reference='WALLETFUND-MISSED1').exists())
        self.assertIn('Credited missed deposit', out.getvalue())

    @patch('wallet.management.commands.sync_zainpay_transactions.zainpay_service.list_account_transactions')
    def test_does_not_recredit_known_transaction(self, mock_list):
        parent_account = make_parent_account('sync-parent-2@example.com')
        WalletFundingRequest.objects.create(
            wallet=parent_account.wallet, reference='WALLETFUND-KNOWN1', amount=Decimal('1000.00'),
        )
        credit_wallet(parent_account.wallet.id, Decimal('1000.00'), 'WALLETFUND-KNOWN1', 'Funding', 'zainpay_checkout_webhook')

        mock_list.return_value = [
            {'transactionType': 'deposit', 'transactionRef': 'WALLETFUND-KNOWN1', 'amount': 100000},
        ]

        out = StringIO()
        call_command('sync_zainpay_transactions', stdout=out)

        parent_account.wallet.refresh_from_db()
        self.assertEqual(parent_account.wallet.balance, Decimal('1000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='WALLETFUND-KNOWN1').count(), 1)

    @patch('wallet.management.commands.sync_zainpay_transactions.zainpay_service.list_account_transactions')
    def test_ignores_deposits_without_a_funding_request(self, mock_list):
        mock_list.return_value = [
            {'transactionType': 'deposit', 'transactionRef': 'SETTLEMENT-UNRELATED', 'amount': 5000000},
        ]

        out = StringIO()
        call_command('sync_zainpay_transactions', stdout=out)

        self.assertFalse(WalletTransaction.objects.filter(reference='SETTLEMENT-UNRELATED').exists())
        self.assertIn('1 unmatched', out.getvalue())

    @patch('wallet.management.commands.sync_zainpay_transactions.zainpay_service.list_account_transactions')
    def test_ignores_non_deposit_transaction_types(self, mock_list):
        parent_account = make_parent_account('sync-parent-3@example.com')
        WalletFundingRequest.objects.create(
            wallet=parent_account.wallet, reference='WALLETFUND-TRANSFERTYPE', amount=Decimal('500.00'),
        )
        mock_list.return_value = [
            {'transactionType': 'transfer', 'transactionRef': 'WALLETFUND-TRANSFERTYPE', 'amount': 50000},
        ]

        out = StringIO()
        call_command('sync_zainpay_transactions', stdout=out)

        self.assertFalse(WalletTransaction.objects.filter(reference='WALLETFUND-TRANSFERTYPE').exists())
