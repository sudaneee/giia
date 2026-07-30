from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from src.models import FeeComponent, FeeStructure, SchoolClass, Section, Session, Student, Term
from wallet.models import DeferredZainpayTransfer, ParentAccount, WalletPayment, WalletTransaction
from wallet.services.exceptions import ZainpayTransferError
from wallet.services.wallet_service import credit_wallet, debit_wallet


def make_deferred_transfer(txn_ref='DEFERRED-1', amount=Decimal('50000.00')):
    user = User.objects.create_user(
        username=f'{txn_ref}@example.com', email=f'{txn_ref}@example.com', password='TestPass123!',
    )
    parent_account = ParentAccount.objects.create(user=user, phone_number='08033334444')
    wallet = parent_account.wallet
    credit_wallet(wallet.id, amount, f'{txn_ref}-fund', 'Funding', 'zainpay_callback_verified')
    wallet_txn = debit_wallet(wallet.id, amount, txn_ref, 'Fee payment', source='wallet_fee_payment')

    section = Section.objects.create(name=f'Section-{txn_ref}')
    school_class = SchoolClass.objects.create(name=f'Class-{txn_ref}', level='JSS 1', arm='A', section=section)
    session = Session.objects.create(
        name=f'{txn_ref}-session', start_date='2025-09-01', end_date='2026-07-31', current=False,
    )
    term = Term.objects.create(name='First Term', session=session, start_date='2025-09-01', end_date='2025-12-15')
    Student.objects.create(admission_number=f'{txn_ref}-adm', first_name='Test', last_name='Student', enrolled_class=school_class)
    fee_structure = FeeStructure.objects.create(
        section=section, session=session, term_group='first', student_type='new',
        transport=False, total_amount=amount,
    )
    FeeComponent.objects.create(fee_structure=fee_structure, name='Tuition', amount=amount)

    wallet_payment = WalletPayment.objects.create(wallet_transaction=wallet_txn, session=session, term=term)

    return DeferredZainpayTransfer.objects.create(
        wallet_payment=wallet_payment, amount=amount, txn_ref=txn_ref,
        narration='Fee payment', failure_reason='insufficient balance',
    )


class RetryDeferredZainpayTransfersCommandTests(TestCase):
    @patch('wallet.management.commands.retry_deferred_zainpay_transfers.zainpay_service.transfer_wallet_to_school')
    def test_marks_completed_on_successful_retry(self, mock_transfer):
        deferred = make_deferred_transfer('DEFERRED-OK')
        mock_transfer.return_value = {'status': 'success', 'totalTxnAmount': '5030000'}

        call_command('retry_deferred_zainpay_transfers', stdout=StringIO())

        deferred.refresh_from_db()
        self.assertTrue(deferred.completed)
        self.assertIsNotNone(deferred.completed_at)
        mock_transfer.assert_called_once_with(Decimal('50000.00'), 'DEFERRED-OK', 'Fee payment')

    @patch('wallet.management.commands.retry_deferred_zainpay_transfers.zainpay_service.transfer_wallet_to_school')
    def test_leaves_still_failing_transfer_pending(self, mock_transfer):
        deferred = make_deferred_transfer('DEFERRED-STILL-SHORT')
        mock_transfer.side_effect = ZainpayTransferError('insufficient balance')

        call_command('retry_deferred_zainpay_transfers', stdout=StringIO())

        deferred.refresh_from_db()
        self.assertFalse(deferred.completed)

    @patch('wallet.management.commands.retry_deferred_zainpay_transfers.zainpay_service.transfer_wallet_to_school')
    def test_already_completed_transfers_are_not_retried(self, mock_transfer):
        deferred = make_deferred_transfer('DEFERRED-DONE')
        deferred.completed = True
        deferred.save(update_fields=['completed'])

        call_command('retry_deferred_zainpay_transfers', stdout=StringIO())

        mock_transfer.assert_not_called()

    @patch('wallet.management.commands.retry_deferred_zainpay_transfers.zainpay_service.transfer_wallet_to_school')
    def test_one_failure_does_not_block_others(self, mock_transfer):
        still_short = make_deferred_transfer('DEFERRED-MULTI-SHORT')
        now_ok = make_deferred_transfer('DEFERRED-MULTI-OK')

        def side_effect(amount, txn_ref, narration):
            if txn_ref == 'DEFERRED-MULTI-SHORT':
                raise ZainpayTransferError('insufficient balance')
            return {'status': 'success', 'totalTxnAmount': str(int(amount * 100))}

        mock_transfer.side_effect = side_effect

        call_command('retry_deferred_zainpay_transfers', stdout=StringIO())

        still_short.refresh_from_db()
        now_ok.refresh_from_db()
        self.assertFalse(still_short.completed)
        self.assertTrue(now_ok.completed)
