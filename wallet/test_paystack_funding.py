import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from wallet.models import ParentAccount, WalletTransaction
from wallet.services import paystack_service
from wallet.test_support import seed_site_context_fixtures


def make_parent_account(email='topup-parent@example.com'):
    user = User.objects.create_user(username=email, email=email, password='TestPass123!')
    return ParentAccount.objects.create(user=user, phone_number='08011112222')


def mock_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    return resp


class CalculateTopupFeeTests(TestCase):
    def test_card_fee_is_percentage_capped(self):
        self.assertEqual(paystack_service.calculate_topup_fee(Decimal('10000.00'), 'card'), Decimal('70.00'))
        self.assertEqual(paystack_service.calculate_topup_fee(Decimal('1000000.00'), 'card'), Decimal('1500.00'))

    def test_bank_transfer_fee_is_flat(self):
        self.assertEqual(paystack_service.calculate_topup_fee(Decimal('5000.00'), 'bank_transfer'), Decimal('300.00'))


@override_settings(PAYSTACK_SECRET_KEY='test-secret-key', SITE_BASE_URL='http://localhost:8000')
class WalletFundInitiateTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.parent_account = make_parent_account()
        self.wallet = self.parent_account.wallet
        self.client = Client()
        self.client.login(username='topup-parent@example.com', password='TestPass123!')

    @patch('wallet.services.paystack_service.requests.post')
    def test_initiates_checkout_with_amount_plus_fee(self, mock_post):
        mock_post.return_value = mock_response({
            'status': True,
            'data': {'authorization_url': 'https://checkout.paystack.com/abc123'},
        })

        response = self.client.post('/parent/wallet/fund/', {
            'amount': '10000.00', 'payment_method': 'card',
        })

        self.assertRedirects(response, 'https://checkout.paystack.com/abc123', fetch_redirect_response=False)

        sent_json = mock_post.call_args.kwargs['json']
        self.assertEqual(sent_json['amount'], int((Decimal('10000.00') + Decimal('70.00')) * 100))
        self.assertEqual(sent_json['metadata']['purpose'], 'wallet_funding')
        self.assertEqual(sent_json['metadata']['wallet_id'], self.wallet.id)
        self.assertEqual(sent_json['metadata']['requested_amount'], '10000.00')

    @patch('wallet.services.paystack_service.requests.post')
    def test_zero_amount_never_calls_paystack(self, mock_post):
        response = self.client.post('/parent/wallet/fund/', {'amount': '0', 'payment_method': 'card'}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_post.assert_not_called()

    @patch('wallet.services.paystack_service.requests.post')
    def test_inactive_wallet_never_calls_paystack(self, mock_post):
        self.wallet.is_active = False
        self.wallet.save(update_fields=['is_active'])

        response = self.client.post('/parent/wallet/fund/', {
            'amount': '5000.00', 'payment_method': 'card',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_post.assert_not_called()


@override_settings(PAYSTACK_SECRET_KEY='test-secret-key')
class WalletFundCallbackTests(TestCase):
    def setUp(self):
        seed_site_context_fixtures()
        self.parent_account = make_parent_account('callback-parent@example.com')
        self.wallet = self.parent_account.wallet
        self.client = Client()
        self.client.login(username='callback-parent@example.com', password='TestPass123!')

    @patch('wallet.services.paystack_service.requests.get')
    def test_credits_wallet_with_requested_amount_not_total_charge(self, mock_get):
        mock_get.return_value = mock_response({
            'status': True,
            'data': {
                'status': 'success',
                'reference': 'WALLETFUND-ABC123',
                'amount': 1007000,  # 10070.00 naira in kobo (amount + fee)
                'metadata': {
                    'purpose': 'wallet_funding',
                    'wallet_id': self.wallet.id,
                    'requested_amount': '10000.00',
                },
            },
        })

        response = self.client.get('/parent/wallet/fund/callback/?reference=WALLETFUND-ABC123', follow=True)

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('10000.00'))
        self.assertTrue(WalletTransaction.objects.filter(reference='WALLETFUND-ABC123').exists())

    @patch('wallet.services.paystack_service.requests.get')
    def test_unsuccessful_status_is_not_credited(self, mock_get):
        mock_get.return_value = mock_response({
            'status': True,
            'data': {'status': 'failed', 'reference': 'WALLETFUND-FAIL1', 'metadata': {}},
        })

        response = self.client.get('/parent/wallet/fund/callback/?reference=WALLETFUND-FAIL1', follow=True)

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    @patch('wallet.services.paystack_service.requests.get')
    def test_duplicate_callback_does_not_double_credit(self, mock_get):
        mock_get.return_value = mock_response({
            'status': True,
            'data': {
                'status': 'success',
                'reference': 'WALLETFUND-DUP1',
                'metadata': {
                    'purpose': 'wallet_funding',
                    'wallet_id': self.wallet.id,
                    'requested_amount': '2000.00',
                },
            },
        })

        self.client.get('/parent/wallet/fund/callback/?reference=WALLETFUND-DUP1')
        self.client.get('/parent/wallet/fund/callback/?reference=WALLETFUND-DUP1')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('2000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='WALLETFUND-DUP1').count(), 1)


@override_settings(PAYSTACK_SECRET_KEY='test-secret-key')
class PaystackFundingWebhookTests(TestCase):
    def setUp(self):
        self.parent_account = make_parent_account('webhook-parent@example.com')
        self.wallet = self.parent_account.wallet
        self.client = Client()

    def _post_webhook(self, body_dict, signature=None):
        body = json.dumps(body_dict).encode('utf-8')
        if signature is None:
            signature = hmac.new(b'test-secret-key', body, hashlib.sha512).hexdigest()
        return self.client.post(
            '/parent/webhooks/paystack/',
            data=body,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

    def _funding_event(self, reference, amount=1000):
        return {
            'event': 'charge.success',
            'data': {
                'reference': reference,
                'amount': amount,
                'metadata': {
                    'purpose': 'wallet_funding',
                    'wallet_id': self.wallet.id,
                    'requested_amount': '1000.00',
                },
            },
        }

    def test_rejects_invalid_signature(self):
        response = self._post_webhook(self._funding_event('WALLETFUND-BADSIG'), signature='wrong-signature')

        self.assertEqual(response.status_code, 401)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_credits_wallet_on_charge_success(self):
        response = self._post_webhook(self._funding_event('WALLETFUND-WH1'))

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000.00'))

    def test_ignores_non_charge_success_events(self):
        response = self._post_webhook({
            'event': 'transfer.success',
            'data': {'reference': 'irrelevant', 'metadata': {}},
        })

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_ignores_events_without_wallet_funding_purpose(self):
        event = self._funding_event('WALLETFUND-OTHER')
        event['data']['metadata']['purpose'] = 'something_else'

        response = self._post_webhook(event)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WalletTransaction.objects.filter(reference='WALLETFUND-OTHER').exists())

    def test_duplicate_webhook_delivery_does_not_double_credit(self):
        event = self._funding_event('WALLETFUND-WH2')

        self._post_webhook(event)
        response2 = self._post_webhook(event)

        self.assertEqual(response2.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='WALLETFUND-WH2').count(), 1)

    @patch('wallet.services.paystack_service.requests.get')
    def test_callback_and_webhook_racing_for_same_reference_credit_only_once(self, mock_get):
        """
        A parent's browser might complete the callback redirect at roughly the
        same time Paystack's webhook arrives for the same top-up. Whichever
        hits credit_wallet() first should win; the other must be a no-op,
        exactly like the Zainpay deposit webhook's own idempotency guarantee.
        """
        mock_get.return_value = mock_response({
            'status': True,
            'data': {
                'status': 'success',
                'reference': 'WALLETFUND-RACE1',
                'metadata': {
                    'purpose': 'wallet_funding',
                    'wallet_id': self.wallet.id,
                    'requested_amount': '1000.00',
                },
            },
        })

        callback_client = Client()
        callback_client.login(username='webhook-parent@example.com', password='TestPass123!')
        callback_client.get('/parent/wallet/fund/callback/?reference=WALLETFUND-RACE1')

        self._post_webhook(self._funding_event('WALLETFUND-RACE1'))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='WALLETFUND-RACE1').count(), 1)
