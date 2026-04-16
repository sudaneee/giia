"""
Management command to sync Paystack payments with local database
Usage: python manage.py sync_paystack_payments --reference=GIIA-XXXX --fix-missing
"""

import requests
from decimal import Decimal
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from src.models import PaymentBatch, Payment, Student, Session, Term, FeeStructure, OtherFeeStructure
from django.db import transaction

class Command(BaseCommand):
    help = 'Sync Paystack payments with local database'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Number of days to look back for transactions'
        )
        parser.add_argument(
            '--reference',
            type=str,
            help='Sync a specific transaction by reference'
        )
        parser.add_argument(
            '--fix-missing',
            action='store_true',
            help='Fix missing payments by creating them'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be synced without making changes'
        )
    
    def handle(self, *args, **options):
        days = options['days']
        reference = options.get('reference')
        fix_missing = options['fix_missing']
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE - No changes will be made ===\n"))
        
        if reference:
            # Sync specific transaction
            self.sync_single_transaction(reference, fix_missing, dry_run)
        else:
            # Sync all transactions from the last X days
            self.sync_recent_transactions(days, fix_missing, dry_run)
    
    def sync_single_transaction(self, reference, fix_missing, dry_run):
        """Sync a single transaction by reference"""
        self.stdout.write(f"Fetching transaction: {reference}")
        
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
        )
        
        if response.status_code == 200:
            data = response.json()
            if data['status']:
                self.process_transaction(data['data'], fix_missing, dry_run, force=True)
            else:
                self.stdout.write(self.style.ERROR(f"Transaction not found: {reference}"))
        else:
            self.stdout.write(self.style.ERROR(f"Failed to fetch: {response.status_code}"))
    
    def sync_recent_transactions(self, days, fix_missing, dry_run):
        """Sync all transactions from the last X days"""
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        self.stdout.write(f"Syncing transactions from {start_date.date()} to {end_date.date()}")
        
        page = 1
        total_synced = 0
        total_created = 0
        total_fixed = 0
        
        while True:
            response = requests.get(
                "https://api.paystack.co/transaction",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
                params={
                    'from': start_date.strftime('%Y-%m-%d'),
                    'to': end_date.strftime('%Y-%m-%d'),
                    'perPage': 50,
                    'page': page
                }
            )
            
            if response.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch page {page}"))
                break
            
            data = response.json()
            transactions = data.get('data', [])
            
            if not transactions:
                break
            
            self.stdout.write(f"\nProcessing page {page} ({len(transactions)} transactions)")
            
            for transaction in transactions:
                result = self.process_transaction(transaction, fix_missing, dry_run)
                if result == 'created':
                    total_created += 1
                elif result == 'synced':
                    total_synced += 1
                elif result == 'fixed':
                    total_fixed += 1
            
            page += 1
            
            if page > data.get('meta', {}).get('pageCount', 0):
                break
        
        self.stdout.write(self.style.SUCCESS(
            f"\n=== SYNC COMPLETE ===\n"
            f"Total synced (already existed): {total_synced}\n"
            f"Total created (missing): {total_created}\n"
            f"Total fixed (had batch but missing payments): {total_fixed}"
        ))
    
    def process_transaction(self, transaction, fix_missing, dry_run, force=False):
        """Process a single transaction and update database"""
        
        reference = transaction['reference']
        amount = Decimal(str(transaction['amount'])) / 100
        status = transaction['status']
        paystack_fee = Decimal(str(transaction.get('fees', 0))) / 100 if transaction.get('fees') else Decimal('0.00')
        payment_date = datetime.strptime(transaction['paid_at'], '%Y-%m-%dT%H:%M:%S.%fZ') if transaction['paid_at'] else timezone.now()
        email = transaction['customer']['email']
        
        self.stdout.write(f"\n--- Processing: {reference} ---")
        self.stdout.write(f"  Amount: ₦{amount}")
        self.stdout.write(f"  Status: {status}")
        self.stdout.write(f"  Email: {email}")
        
        if status != 'success':
            self.stdout.write(f"  ⚠ Transaction not successful, skipping")
            return 'ignored'
        
        # Check if batch exists
        batch = PaymentBatch.objects.filter(reference=reference).first()
        
        if batch:
            # Check if batch has payments
            has_payments = Payment.objects.filter(payment_batch=batch).exists()
            
            if has_payments:
                self.stdout.write(f"  ✓ Batch exists with payments, skipping")
                return 'synced'
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠ Batch exists but NO payments found!"))
                
                if fix_missing or force:
                    self.stdout.write(f"  Fixing missing payments for batch...")
                    
                    if dry_run:
                        self.stdout.write(f"    [DRY RUN] Would create payments for batch")
                        return 'would_fix'
                    
                    # Create payments for this batch
                    return self.create_payments_for_batch(batch, transaction, amount, email)
                else:
                    self.stdout.write(f"  Use --fix-missing to create payments")
                    return 'needs_fix'
        
        # Batch doesn't exist - create new
        self.stdout.write(f"  Creating new batch...")
        
        if dry_run:
            self.stdout.write(f"    [DRY RUN] Would create batch and payments")
            return 'would_create'
        
        return self.create_new_batch_and_payments(transaction, amount, paystack_fee, payment_date, email)
    
    def create_payments_for_batch(self, batch, transaction, amount, email):
        """Create payment records for an existing batch"""
        
        try:
            with transaction.atomic():
                # Get session and term from batch or use current
                session = batch.session
                term = batch.term
                
                if not session:
                    session = Session.objects.filter(current=True).first()
                if not term and session:
                    term = Term.objects.filter(session=session).first()
                
                # Try to find student by email or create generic payment
                # First, try to find student by email in guardians
                from src.models import Guardian
                
                guardian = Guardian.objects.filter(email=email).first()
                students = []
                
                if guardian:
                    students = list(guardian.student_set.all())
                    self.stdout.write(f"    Found guardian with {len(students)} students")
                
                if not students:
                    # Try to find student by email directly
                    student = Student.objects.filter(email=email).first()
                    if student:
                        students = [student]
                        self.stdout.write(f"    Found student by email: {student.admission_number}")
                
                if students:
                    # Distribute payment among students
                    amount_per_student = amount / len(students)
                    
                    for student in students:
                        payment = Payment.objects.create(
                            student=student,
                            transaction_reference=f"{batch.reference}-{student.id}",
                            amount_paid=amount_per_student.quantize(Decimal('0.01')),
                            payment_method=batch.payment_channel if batch.payment_channel else 'credit_card',
                            status='paid',
                            session=session,
                            term=term,
                            payment_batch=batch,
                            payment_date=timezone.now().date(),
                            payment_metadata={
                                'paystack_reference': transaction['reference'],
                                'recovered': True,
                                'recovered_at': timezone.now().isoformat(),
                            }
                        )
                        self.stdout.write(f"    Created payment for {student.first_name} {student.last_name}: ₦{payment.amount_paid}")
                else:
                    # Create a generic payment record that needs manual assignment
                    payment = Payment.objects.create(
                        student=None,
                        transaction_reference=batch.reference,
                        amount_paid=amount,
                        payment_method=batch.payment_channel if batch.payment_channel else 'credit_card',
                        status='paid',
                        session=session,
                        term=term,
                        payment_batch=batch,
                        payment_date=timezone.now().date(),
                        payment_metadata={
                            'paystack_reference': transaction['reference'],
                            'recovered': True,
                            'needs_manual_assignment': True,
                            'customer_email': email,
                            'recovered_at': timezone.now().isoformat(),
                        }
                    )
                    self.stdout.write(self.style.WARNING(f"    Created generic payment - needs manual assignment"))
                
                # Update batch status if needed
                if batch.status != 'success':
                    batch.status = 'success'
                    batch.save()
                
                self.stdout.write(self.style.SUCCESS(f"  ✅ Successfully created {len(students) if students else 1} payment(s)"))
                return 'fixed'
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error creating payments: {str(e)}"))
            import traceback
            traceback.print_exc()
            return 'error'
    
    def create_new_batch_and_payments(self, transaction, amount, paystack_fee, payment_date, email):
        """Create a new batch and payment records"""
        
        try:
            with transaction.atomic():
                # Get current session and term
                session = Session.objects.filter(current=True).first()
                term = None
                if session:
                    term = Term.objects.filter(session=session).first()
                
                # Create batch
                batch = PaymentBatch.objects.create(
                    reference=transaction['reference'],
                    parent_email=email,
                    amount_paid=amount,
                    session=session,
                    term=term,
                    payment_channel=transaction.get('channel', 'card'),
                    status='success',
                    paystack_fee=paystack_fee,
                    created_at=payment_date
                )
                
                self.stdout.write(f"    Created batch ID: {batch.id}")
                
                # Try to find student by email
                from src.models import Guardian
                
                guardian = Guardian.objects.filter(email=email).first()
                students = []
                
                if guardian:
                    students = list(guardian.student_set.all())
                    self.stdout.write(f"    Found guardian with {len(students)} students")
                
                if not students:
                    student = Student.objects.filter(email=email).first()
                    if student:
                        students = [student]
                        self.stdout.write(f"    Found student by email: {student.admission_number}")
                
                if students:
                    amount_per_student = amount / len(students)
                    
                    for student in students:
                        Payment.objects.create(
                            student=student,
                            transaction_reference=f"{batch.reference}-{student.id}",
                            amount_paid=amount_per_student.quantize(Decimal('0.01')),
                            payment_method=transaction.get('channel', 'credit_card'),
                            status='paid',
                            session=session,
                            term=term,
                            payment_batch=batch,
                            payment_date=payment_date.date(),
                            payment_metadata={
                                'paystack_reference': transaction['reference'],
                                'recovered': True,
                            }
                        )
                        self.stdout.write(f"    Created payment for {student.first_name} {student.last_name}")
                else:
                    Payment.objects.create(
                        student=None,
                        transaction_reference=batch.reference,
                        amount_paid=amount,
                        payment_method=transaction.get('channel', 'credit_card'),
                        status='paid',
                        session=session,
                        term=term,
                        payment_batch=batch,
                        payment_date=payment_date.date(),
                        payment_metadata={
                            'paystack_reference': transaction['reference'],
                            'needs_manual_assignment': True,
                            'customer_email': email,
                        }
                    )
                    self.stdout.write(self.style.WARNING(f"    Created generic payment - needs manual assignment"))
                
                return 'created'
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error: {str(e)}"))
            return 'error'