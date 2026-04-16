"""
Utility functions to reconcile Paystack payments
Run with: python manage.py shell
"""

import requests
from decimal import Decimal
from django.conf import settings
from src.models import PaymentBatch, Payment, Student

def fetch_transaction_by_reference(reference):
    """Fetch a single transaction from Paystack"""
    response = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
    )
    
    if response.status_code == 200:
        return response.json().get('data')
    return None


def fetch_transactions_by_email(email, days=30):
    """Fetch all transactions for a specific email"""
    from datetime import datetime, timedelta
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    response = requests.get(
        "https://api.paystack.co/transaction",
        headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        params={
            'customer': email,
            'from': start_date.strftime('%Y-%m-%d'),
            'to': end_date.strftime('%Y-%m-%d'),
            'perPage': 50
        }
    )
    
    if response.status_code == 200:
        return response.json().get('data', [])
    return []


def reconcile_payment_by_reference(reference, student_admission_number=None, fee_type='school_fees'):
    """Manually reconcile a payment by reference"""
    
    transaction = fetch_transaction_by_reference(reference)
    
    if not transaction:
        print(f"Transaction {reference} not found")
        return False
    
    if transaction['status'] != 'success':
        print(f"Transaction {reference} is not successful")
        return False
    
    # Check if already processed
    if PaymentBatch.objects.filter(reference=reference).exists():
        print(f"Transaction {reference} already in database")
        return True
    
    amount = Decimal(str(transaction['amount'])) / 100
    email = transaction['customer']['email']
    
    print(f"Found transaction: {reference} - ₦{amount} - {email}")
    
    if student_admission_number:
        student = Student.objects.filter(admission_number=student_admission_number).first()
        if not student:
            print(f"Student not found: {student_admission_number}")
            return False
        
        # Create batch
        from src.models import Session, Term, PaymentBatch, Payment
        
        current_session = Session.objects.filter(current=True).first()
        current_term = Term.objects.filter(session=current_session).first()
        
        batch = PaymentBatch.objects.create(
            reference=reference,
            parent_email=email,
            amount_paid=amount,
            session=current_session,
            term=current_term,
            payment_channel=transaction.get('channel', 'card'),
            status='success',
            paystack_fee=Decimal(str(transaction.get('fees', 0))) / 100
        )
        
        # Create payment record
        Payment.objects.create(
            student=student,
            transaction_reference=reference,
            amount_paid=amount,
            payment_method=transaction.get('channel', 'card'),
            status='paid',
            session=current_session,
            term=current_term,
            payment_batch=batch,
            payment_metadata={
                'paystack_transaction': transaction,
                'manually_reconciled': True,
                'reconciled_at': timezone.now().isoformat(),
            }
        )
        
        print(f"Created payment for {student.first_name} {student.last_name} - ₦{amount}")
        return True
    
    return False


def list_unreconciled_payments():
    """List payments that need manual reconciliation"""
    
    # Find batches without any associated payments
    batches_without_payments = PaymentBatch.objects.filter(
        payments__isnull=True,
        status='success'
    )
    
    print("Batches without payments:")
    for batch in batches_without_payments:
        print(f"  {batch.reference} - {batch.parent_email} - ₦{batch.amount_paid}")
    
    # Find payments without student assignment
    payments_without_student = Payment.objects.filter(student__isnull=True)
    
    print("\nPayments without student assignment:")
    for payment in payments_without_student:
        print(f"  {payment.transaction_reference} - ₦{payment.amount_paid} - Needs manual assignment")
    
    return {
        'batches_without_payments': batches_without_payments,
        'payments_without_student': payments_without_student
    }