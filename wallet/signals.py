from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ParentAccount, Wallet


@receiver(post_save, sender=ParentAccount)
def create_wallet_for_parent_account(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(parent_account=instance)
