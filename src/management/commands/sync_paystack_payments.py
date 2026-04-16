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
from src.models import PaymentBatch, Payment, Student, Session, Term, Guardian
from django.db import transaction as db_transaction
import json

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
            self.sync_single_transaction(reference, fix_missing, dry_run)
        else:
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
            
            for txn in transactions:
                result = self.process_transaction(txn, fix_missing, dry_run)
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
    
    def extract_admission_numbers_from_metadata(self, batch):
        """Extract admission numbers from batch metadata"""
        admission_numbers = []
        
        # Check payment_metadata field
        if batch.payment_metadata:
            metadata = batch.payment_metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    pass
            
            # Look for admission numbers in various places
            if 'breakdown' in metadata:
                for item in metadata['breakdown']:
                    if 'admission_number' in item:
                        admission_numbers.append(item['admission_number'])
                    elif 'student' in item and 'admission_number' in item['student']:
                        admission_numbers.append(item['student']['admission_number'])
            
            if 'parent_data' in metadata and 'breakdown' in metadata['parent_data']:
                for item in metadata['parent_data']['breakdown']:
                    if 'admission_number' in item:
                        admission_numbers.append(item['admission_number'])
        
        # Also check if there are any pending payments with metadata
        from src.models import Payment
        pending_payments = Payment.objects.filter(payment_batch=batch)
        for payment in pending_payments:
            if payment.payment_metadata and 'admission_number' in payment.payment_metadata:
                admission_numbers.append(payment.payment_metadata['admission_number'])
        
        return list(set(admission_numbers))  # Remove duplicates
    
    def process_transaction(self, paystack_txn, fix_missing, dry_run, force=False):
        """Process a single transaction and update database"""
        
        reference = paystack_txn['reference']
        amount = Decimal(str(paystack_txn['amount'])) / 100
        status = paystack_txn['status']
        paystack_fee = Decimal(str(paystack_txn.get('fees', 0))) / 100 if paystack_txn.get('fees') else Decimal('0.00')
        payment_date = datetime.strptime(paystack_txn['paid_at'], '%Y-%m-%dT%H:%M:%S.%fZ') if paystack_txn['paid_at'] else timezone.now()
        email = paystack_txn['customer']['email']
        
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
                    
                    return self.create_payments_for_batch(batch, paystack_txn, amount, email)
                else:
                    self.stdout.write(f"  Use --fix-missing to create payments")
                    return 'needs_fix'
        
        # Batch doesn't exist - create new
        self.stdout.write(f"  Creating new batch...")
        
        if dry_run:
            self.stdout.write(f"    [DRY RUN] Would create batch and payments")
            return 'would_create'
        
        return self.create_new_batch_and_payments(paystack_txn, amount, paystack_fee, payment_date, email)
    
    def create_payments_for_batch(self, batch, paystack_txn, amount, email):
        """Create payment records for an existing batch"""
        
        try:
            with db_transaction.atomic():
                # Get session and term
                session = batch.session
                term = batch.term
                
                if not session:
                    session = Session.objects.filter(current=True).first()
                if not term and session:
                    term = Term.objects.filter(session=session).first()
                
                # Try to extract admission numbers from metadata
                admission_numbers = self.extract_admission_numbers_from_metadata(batch)
                
                students = []
                
                if admission_numbers:
                    self.stdout.write(f"    Found admission numbers in metadata: {admission_numbers}")
                    for adm_no in admission_numbers:
                        student = Student.objects.filter(admission_number=adm_no).first()
                        if student:
                            students.append(student)
                            self.stdout.write(f"    Found student: {student.first_name} {student.last_name} ({adm_no})")
                        else:
                            self.stdout.write(self.style.WARNING(f"    Student not found for admission number: {adm_no}"))
                
                # If no students from metadata, try by email
                if not students:
                    guardian = Guardian.objects.filter(email=email).first()
                    if guardian:
                        students = list(guardian.student_set.all())
                        self.stdout.write(f"    Found guardian with {len(students)} students")
                
                if not students:
                    student = Student.objects.filter(email=email).first()
                    if student:
                        students = [student]
                        self.stdout.write(f"    Found student by email: {student.admission_number}")
                
                if students:
                    # Distribute payment among students
                    amount_per_student = (amount / len(students)).quantize(Decimal('0.01'))
                    
                    for student in students:
                        payment = Payment.objects.create(
                            student=student,
                            transaction_reference=f"{batch.reference}-{student.id}",
                            amount_paid=amount_per_student,
                            payment_method=batch.payment_channel if batch.payment_channel else 'credit_card',
                            status='paid',
                            session=session,
                            term=term,
                            payment_batch=batch,
                            payment_date=timezone.now().date(),
                            payment_metadata={
                                'paystack_reference': paystack_txn['reference'],
                                'admission_number': student.admission_number,
                                'recovered': True,
                                'recovered_at': timezone.now().isoformat(),
                            }
                        )
                        self.stdout.write(f"    ✅ Created payment for {student.first_name} {student.last_name}: ₦{payment.amount_paid}")
                else:
                    self.stdout.write(self.style.ERROR(f"    ❌ No students found! Cannot create payment without student."))
                    self.stdout.write(f"    Email: {email}")
                    self.stdout.write(f"    Admission numbers from metadata: {admission_numbers}")
                    return 'error'
                
                # Update batch status
                if batch.status != 'success':
                    batch.status = 'success'
                    batch.save()
                
                self.stdout.write(self.style.SUCCESS(f"  ✅ Successfully created {len(students)} payment(s)"))
                return 'fixed'
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ❌ Error creating payments: {str(e)}"))
            import traceback
            traceback.print_exc()
            return 'error'
    
    def create_new_batch_and_payments(self, paystack_txn, amount, paystack_fee, payment_date, email):
        """Create a new batch and payment records"""
        
        try:
            with db_transaction.atomic():
                session = Session.objects.filter(current=True).first()
                term = Term.objects.filter(session=session).first() if session else None
                
                batch = PaymentBatch.objects.create(
                    reference=paystack_txn['reference'],
                    parent_email=email,
                    amount_paid=amount,
                    session=session,
                    term=term,
                    payment_channel=paystack_txn.get('channel', 'card'),
                    status='success',
                    paystack_fee=paystack_fee,
                    created_at=payment_date
                )
                
                self.stdout.write(f"    Created batch ID: {batch.id}")
                
                # Try to find student by email
                student = Student.objects.filter(email=email).first()
                
                if student:
                    Payment.objects.create(
                        student=student,
                        transaction_reference=batch.reference,
                        amount_paid=amount,
                        payment_method=paystack_txn.get('channel', 'credit_card'),
                        status='paid',
                        session=session,
                        term=term,
                        payment_batch=batch,
                        payment_date=payment_date.date(),
                        payment_metadata={
                            'paystack_reference': paystack_txn['reference'],
                            'recovered': True,
                        }
                    )
                    self.stdout.write(f"    ✅ Created payment for {student.first_name} {student.last_name}")
                else:
                    self.stdout.write(self.style.ERROR(f"    ❌ No student found for email: {email}"))
                    return 'error'
                
                return 'created'
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ❌ Error: {str(e)}"))
            return 'error'