import threading
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import OperationalError, connection
from django.test import TestCase, TransactionTestCase

from wallet.models import ParentAccount, WalletTransaction
from wallet.services.exceptions import InsufficientFundsError, WalletInactiveError
from wallet.services.wallet_service import credit_wallet, debit_wallet


def make_parent_account(email='parent@example.com'):
    user = User.objects.create_user(username=email, email=email, password='TestPass123!')
    return ParentAccount.objects.create(
        user=user,
        phone_number='08000000000',
        title='Mr',
        gender='M',
        date_of_birth='1990-01-01',
        bvn='12345678901',
        address='1 Test Street',
        state='Kano',
    )


class WalletServiceTests(TestCase):
    def setUp(self):
        self.parent_account = make_parent_account()
        self.wallet = self.parent_account.wallet  # auto-created via post_save signal

    def test_wallet_auto_created_with_zero_balance(self):
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_credit_wallet_increases_balance_and_writes_ledger(self):
        txn = credit_wallet(self.wallet.id, Decimal('5000.00'), 'ref-credit-1', 'Bank transfer', 'zainpay_webhook')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('5000.00'))
        self.assertEqual(txn.transaction_type, WalletTransaction.CREDIT)
        self.assertEqual(txn.balance_before, Decimal('0.00'))
        self.assertEqual(txn.balance_after, Decimal('5000.00'))
        self.assertEqual(self.wallet.authoritative_balance, Decimal('5000.00'))

    def test_debit_wallet_decreases_balance_and_writes_ledger(self):
        credit_wallet(self.wallet.id, Decimal('10000.00'), 'ref-credit-2', 'Funding', 'zainpay_webhook')
        txn = debit_wallet(self.wallet.id, Decimal('3000.00'), 'ref-debit-1', 'School fees', 'wallet_fee_payment')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('7000.00'))
        self.assertEqual(txn.transaction_type, WalletTransaction.DEBIT)
        self.assertEqual(self.wallet.authoritative_balance, Decimal('7000.00'))

    def test_credit_is_idempotent_on_duplicate_reference(self):
        credit_wallet(self.wallet.id, Decimal('1000.00'), 'dup-ref', 'First', 'zainpay_webhook')
        credit_wallet(self.wallet.id, Decimal('1000.00'), 'dup-ref', 'Duplicate webhook retry', 'zainpay_webhook')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='dup-ref').count(), 1)

    def test_debit_is_idempotent_on_duplicate_reference(self):
        credit_wallet(self.wallet.id, Decimal('5000.00'), 'fund-1', 'Funding', 'zainpay_webhook')
        debit_wallet(self.wallet.id, Decimal('2000.00'), 'dup-debit-ref', 'Fee payment', 'wallet_fee_payment')
        debit_wallet(self.wallet.id, Decimal('2000.00'), 'dup-debit-ref', 'Retry', 'wallet_fee_payment')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('3000.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='dup-debit-ref').count(), 1)

    def test_debit_raises_insufficient_funds(self):
        credit_wallet(self.wallet.id, Decimal('1000.00'), 'fund-2', 'Funding', 'zainpay_webhook')
        with self.assertRaises(InsufficientFundsError):
            debit_wallet(self.wallet.id, Decimal('5000.00'), 'overdraft-ref', 'Too much', 'wallet_fee_payment')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000.00'))
        self.assertFalse(WalletTransaction.objects.filter(reference='overdraft-ref').exists())

    def test_debit_raises_when_wallet_inactive(self):
        credit_wallet(self.wallet.id, Decimal('1000.00'), 'fund-3', 'Funding', 'zainpay_webhook')
        self.wallet.is_active = False
        self.wallet.save(update_fields=['is_active'])
        with self.assertRaises(WalletInactiveError):
            debit_wallet(self.wallet.id, Decimal('500.00'), 'inactive-debit-ref', 'Should fail', 'wallet_fee_payment')

    def test_credit_raises_when_wallet_inactive(self):
        self.wallet.is_active = False
        self.wallet.save(update_fields=['is_active'])
        with self.assertRaises(WalletInactiveError):
            credit_wallet(self.wallet.id, Decimal('500.00'), 'inactive-credit-ref', 'Should fail', 'zainpay_webhook')


class WalletConcurrencyTests(TransactionTestCase):
    """
    Uses TransactionTestCase (not TestCase) because real concurrent threads
    need their writes to actually commit and be visible across connections -
    TestCase wraps each test in one outer transaction, which would hide
    exactly the race conditions this test is meant to catch.
    """

    def setUp(self):
        self.parent_account = make_parent_account('concurrent@example.com')
        self.wallet = self.parent_account.wallet
        credit_wallet(self.wallet.id, Decimal('1000.00'), 'seed-fund', 'Initial funding', 'zainpay_webhook')

    def test_concurrent_debits_cannot_overdraw_the_wallet(self):
        """
        Ten threads each try to debit 600 from a wallet that only has 1000.
        At most one should succeed; none should be able to push the balance
        negative, and the final balance must always match the ledger sum.
        """
        results = []
        lock = threading.Lock()

        def attempt_debit(i):
            try:
                debit_wallet(self.wallet.id, Decimal('600.00'), f'race-debit-{i}', 'Race test', 'wallet_fee_payment')
                outcome = 'success'
            except InsufficientFundsError:
                outcome = 'insufficient_funds'
            except OperationalError as e:
                outcome = f'operational_error: {e}'
            finally:
                connection.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt_debit, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.wallet.refresh_from_db()

        self.assertEqual(len(results), 10, f'Expected all 10 threads to resolve cleanly, got: {results}')
        operational_errors = [r for r in results if r.startswith('operational_error')]
        self.assertEqual(operational_errors, [], f'No thread should hit a raw DB lock error: {operational_errors}')

        successes = results.count('success')
        self.assertEqual(successes, 1, f'Expected exactly 1 successful debit, got {successes}: {results}')
        self.assertEqual(results.count('insufficient_funds'), 9)
        self.assertEqual(self.wallet.balance, Decimal('400.00'))
        self.assertGreaterEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(self.wallet.balance, self.wallet.authoritative_balance)

    def test_concurrent_credits_with_same_reference_apply_only_once(self):
        """
        Five threads all retry-deliver the same webhook reference concurrently.
        Only one credit should ever be applied.
        """
        def attempt_credit():
            try:
                credit_wallet(self.wallet.id, Decimal('2000.00'), 'webhook-retry-ref', 'Deposit', 'zainpay_webhook')
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt_credit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('3000.00'))  # 1000 seed + 2000 once
        self.assertEqual(WalletTransaction.objects.filter(reference='webhook-retry-ref').count(), 1)
        self.assertEqual(self.wallet.balance, self.wallet.authoritative_balance)
