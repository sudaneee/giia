"""
Management command to sync Paystack payments with local database
Usage: python manage.py sync_paystack_payments --days=30 --fix-missing
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
                self.process_transaction(data['data'], fix_missing, dry_run)
            else:
                self.stdout.write(self.style.ERROR(f"Transaction not found: {reference}"))
        else:
            self.stdout.write(self.style.ERROR(f"Failed to fetch: {response.status_code}"))
    
    def sync_recent_transactions(self, days, fix_missing, dry_run):
        """Sync all transactions from the last X days"""
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        self.stdout.write(f"Syncing transactions from {start_date.date()} to {end_date.date()}")
        
        # Fetch transactions from Paystack
        page = 1
        total_synced = 0
        total_created = 0
        
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
            
            page += 1
            
            # Check if we've reached the last page
            if page > data.get('meta', {}).get('pageCount', 0):
                break
        
        self.stdout.write(self.style.SUCCESS(
            f"\n=== SYNC COMPLETE ===\n"
            f"Total synced (already existed): {total_synced}\n"
            f"Total created (missing): {total_created}"
        ))
    
    def process_transaction(self, transaction, fix_missing, dry_run):
        """Process a single transaction and update database"""
        
        reference = transaction['reference']
        amount = Decimal(str(transaction['amount'])) / 100
        status = transaction['status']
        paystack_fee = Decimal(str(transaction.get('fees', 0))) / 100 if transaction.get('fees') else Decimal('0.00')
        payment_date = datetime.strptime(transaction['paid_at'], '%Y-%m-%dT%H:%M:%S.%fZ') if transaction['paid_at'] else timezone.now()
        
        # Check if batch already exists
        batch_exists = PaymentBatch.objects.filter(reference=reference).exists()
        
        if batch_exists and not fix_missing:
            self.stdout.write(f"  ✓ Already exists: {reference}")
            return 'synced'
        
        if batch_exists and fix_missing:
            self.stdout.write(f"  Updating existing batch: {reference}")
            if not dry_run:
                PaymentBatch.objects.filter(reference=reference).update(
                    status='success' if status == 'success' else 'failed',
                    paystack_fee=paystack_fee
                )
            return 'updated'
        
        if status != 'success':
            self.stdout.write(f"  ⚠ Transaction not successful: {reference} ({status})")
            return 'ignored'
        
        self.stdout.write(f"  Processing new transaction: {reference} - ₦{amount}")
        
        if dry_run:
            self.stdout.write(f"    [DRY RUN] Would create batch and payments")
            return 'would_create'
        
        # Try to extract metadata from transaction
        metadata = transaction.get('metadata', {})
        custom_fields = metadata.get('custom_fields', [])
        
        # Extract payment type from metadata
        payment_type = None
        for field in custom_fields:
            if field.get('variable_name') == 'payment_type':
                payment_type = field.get('value')
                break
        
        # Get session and term if available
        session = None
        term = None
        
        # Try to find session from transaction metadata or use current
        current_session = Session.objects.filter(current=True).first()
        if current_session:
            session = current_session
            term = Term.objects.filter(session=session).first()
        
        with transaction.atomic():
            # Create payment batch
            batch = PaymentBatch.objects.create(
                reference=reference,
                parent_email=transaction['customer']['email'],
                amount_paid=amount,
                session=session,
                term=term,
                payment_channel=transaction.get('channel', 'card'),
                status='success',
                paystack_fee=paystack_fee,
                created_at=payment_date
            )
            
            self.stdout.write(f"    Created batch ID: {batch.id}")
            
            # Try to determine the payment type and create payments
            # Since we don't have the original breakdown, we need to reconstruct
            # This is where you'd add logic to create payments based on your business rules
            
            # Option 1: Check if there's a pending batch with same email
            # This helps recover payments that were initiated but not completed
            pending_batch = PaymentBatch.objects.filter(
                parent_email=transaction['customer']['email'],
                status='pending',
                amount_paid=amount,
                created_at__date=payment_date.date()
            ).first()
            
            if pending_batch:
                self.stdout.write(f"    Found matching pending batch: {pending_batch.reference}")
                # Copy over any existing payments from pending batch
                for payment in Payment.objects.filter(payment_batch=pending_batch):
                    payment.payment_batch = batch
                    payment.status = 'paid'
                    payment.transaction_reference = f"{reference}-{payment.id}"
                    payment.save()
                
                # Delete the pending batch
                pending_batch.delete()
                self.stdout.write(f"    Migrated payments from pending batch")
            else:
                # Option 2: Create a generic payment record
                # This is a fallback - you'll need to manually assign later
                self.stdout.write(self.style.WARNING(
                    f"    No pending batch found. Payment recorded but needs manual assignment."
                ))
                
                # Create a placeholder payment record
                Payment.objects.create(
                    student=None,  # Needs manual assignment
                    transaction_reference=reference,
                    amount_paid=amount,
                    payment_method=transaction.get('channel', 'card'),
                    status='paid',
                    session=session,
                    term=term,
                    payment_batch=batch,
                    payment_date=payment_date.date(),
                    payment_metadata={
                        'paystack_transaction': transaction,
                        'needs_manual_assignment': True,
                        'customer_email': transaction['customer']['email'],
                    }
                )
            
            return 'created'