# Generated migration - create this file
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('src', '0001_initial'),  # Change this to your last migration file
    ]

    operations = [
        migrations.AddField(
            model_name='paymentbatch',
            name='payment_metadata',
            field=models.JSONField(blank=True, help_text='Stores full payment details for audit/webhook', null=True),
        ),
        migrations.AddField(
            model_name='paymentbatch',
            name='webhook_processed',
            field=models.BooleanField(default=False, help_text='Tracks if webhook has been fully processed'),
        ),
        migrations.AddField(
            model_name='paymentbatch',
            name='webhook_attempts',
            field=models.IntegerField(default=0, help_text='Number of webhook processing attempts'),
        ),
        migrations.AddField(
            model_name='paymentbatch',
            name='last_webhook_attempt',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='payment_metadata',
            field=models.JSONField(blank=True, help_text='Per-payment metadata for reconciliation', null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='external_reference',
            field=models.CharField(blank=True, help_text='External system reference (e.g., ERP ID)', max_length=100, null=True),
        ),
    ]