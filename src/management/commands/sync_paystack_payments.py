from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import requests
from decimal import Decimal
from src.models import PaymentBatch, Payment, Student, FeeStructure, OtherFeeStructure
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync pending Paystack payments for reconciliation'
    
    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7, help='Days to look back')
        parser.add_argument('--retry-failed', action='store_true', help='Retry failed webhooks')
        parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    
    def handle(self, *args, **options):
        days = options['days']
        retry_failed = options['retry_failed']
        dry_run = options['dry_run']
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        pending_batches = PaymentBatch.objects.filter(
            status='pending',
            created_at__gte=cutoff_date,
            webhook_processed=False,
            webhook_attempts__lt=5
        )
        
        self.stdout.write(f"Found {pending_batches.count()} pending batches to sync")
        
        if dry_run:
            self.stdout.write("=== DRY RUN MODE - No changes will be made ===")
        
        for batch in pending_batches:
            self.stdout.write(f"Processing: {batch.reference}")
            
            try:
                # Verify with Paystack API
                response = requests.get(
                    f"https://api.paystack.co/transaction/verify/{batch.reference}",
                    headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()['data']
                    
                    if data['status'] == 'success':
                        self.stdout.write(self.style.SUCCESS(f"  Payment successful: {batch.reference}"))
                        
                        if not dry_run:
                            # Process the payment
                            payment_type = batch.payment_metadata.get('payment_type') if batch.payment_metadata else None
                            
                            from src.views import process_school_fees_webhook, process_other_fees_webhook
                            
                            try:
                                if payment_type == 'school_fees':
                                    process_school_fees_webhook(batch, data)
                                elif payment_type == 'other_fees':
                                    process_other_fees_webhook(batch, data)
                                else:
                                    # Legacy handling
                                    batch.status = 'success'
                                    batch.save()
                                
                                batch.webhook_processed = True
                                batch.status = 'success'
                                batch.save()
                                
                                self.stdout.write(self.style.SUCCESS(f"  Synced successfully"))
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f"  Error processing: {str(e)}"))
                                batch.webhook_attempts += 1
                                batch.last_webhook_attempt = timezone.now()
                                batch.save()
                    
                    elif data['status'] == 'failed':
                        self.stdout.write(self.style.WARNING(f"  Payment failed: {batch.reference}"))
                        
                        if not dry_run:
                            batch.status = 'failed'
                            batch.save()
                    else:
                        self.stdout.write(f"  Payment pending: {batch.reference}")
                        
                        if retry_failed and batch.webhook_attempts < 5:
                            if not dry_run:
                                batch.webhook_attempts += 1
                                batch.last_webhook_attempt = timezone.now()
                                batch.save()
                                self.stdout.write(f"  Retry #{batch.webhook_attempts} scheduled")
                
                else:
                    self.stdout.write(self.style.ERROR(f"  API error: {response.status_code}"))
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Exception: {str(e)}"))
        
        # Summary
        synced = PaymentBatch.objects.filter(webhook_processed=True, created_at__gte=cutoff_date).count()
        failed = PaymentBatch.objects.filter(status='failed', created_at__gte=cutoff_date).count()
        pending = PaymentBatch.objects.filter(status='pending', webhook_processed=False, created_at__gte=cutoff_date).count()
        
        self.stdout.write("\n=== SUMMARY ===")
        self.stdout.write(f"Synced: {synced}")
        self.stdout.write(f"Failed: {failed}")
        self.stdout.write(f"Still Pending: {pending}")
        
        if not dry_run and pending > 0:
            self.stdout.write(self.style.WARNING(f"\n⚠️ {pending} payments still pending. Run again later."))