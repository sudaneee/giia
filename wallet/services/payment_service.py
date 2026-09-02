from django.db import transaction
from django.utils import timezone

from src.models import Payment

from wallet.services.payment_line_item_service import create_line_items_for_payment


def create_payment_record(student, amount, payment_method, session, term, transaction_reference,
                            fee_structure=None, other_fee=None, batch=None, payment_metadata=None):
    """
    Creates a single Payment record. Shared by the Paystack webhook, the Paystack
    callback, and (from Phase 5 onward) the wallet payment flow, so every path that
    marks a fee as paid does it through one place.

    Also creates the PaymentLineItem row(s) breaking the payment down by item
    type (uniform, transportation, tuition, ...), in the same transaction so
    a payment can never be saved without its breakdown.
    """
    with transaction.atomic():
        payment = Payment.objects.create(
            student=student,
            transaction_reference=transaction_reference,
            fee_structure=fee_structure,
            other_fee=other_fee,
            amount_paid=amount,
            payment_method=payment_method,
            status='paid',
            session=session,
            term=term,
            payment_batch=batch,
            payment_date=timezone.now().date(),
            payment_metadata=payment_metadata or {},
        )
        create_line_items_for_payment(payment)
    return payment
