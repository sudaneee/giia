from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum


class ParentAccount(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='parent_account',
    )
    phone_number = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        full_name = self.user.get_full_name()
        return full_name or self.user.email or self.user.username


class ParentStudentLink(models.Model):
    parent_account = models.ForeignKey(
        ParentAccount,
        on_delete=models.CASCADE,
        related_name='student_links',
    )
    student = models.ForeignKey(
        'src.Student',
        on_delete=models.CASCADE,
        related_name='parent_links',
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('parent_account', 'student')

    def __str__(self):
        return f"{self.parent_account} -> {self.student}"


class Wallet(models.Model):
    parent_account = models.OneToOneField(
        ParentAccount,
        on_delete=models.PROTECT,
        related_name='wallet',
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.parent_account} Wallet - ₦{self.balance}"

    @property
    def authoritative_balance(self):
        """
        SUM(completed credits) - SUM(completed debits) from the ledger.
        This is the source of truth; `balance` above is a cache kept in sync
        by wallet_service.credit_wallet()/debit_wallet() only.
        """
        credits = self.transactions.filter(
            transaction_type=WalletTransaction.CREDIT,
            status=WalletTransaction.COMPLETED,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        debits = self.transactions.filter(
            transaction_type=WalletTransaction.DEBIT,
            status=WalletTransaction.COMPLETED,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        return credits - debits


class WalletTransaction(models.Model):
    CREDIT = 'credit'
    DEBIT = 'debit'
    TRANSACTION_TYPE_CHOICES = [
        (CREDIT, 'Credit'),
        (DEBIT, 'Debit'),
    ]

    COMPLETED = 'completed'
    FAILED = 'failed'
    STATUS_CHOICES = [
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='transactions')
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_before = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)
    narration = models.CharField(max_length=255)
    source = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=COMPLETED)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['wallet', '-created_at']),
        ]

    def __str__(self):
        return f"{self.get_transaction_type_display()} of ₦{self.amount} - {self.wallet}"


class VirtualAccount(models.Model):
    wallet = models.OneToOneField(Wallet, on_delete=models.PROTECT, related_name='virtual_account')
    account_number = models.CharField(max_length=20, unique=True)
    account_name = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=100)
    bank_code = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.account_number} ({self.bank_name}) - {self.wallet.parent_account}"


class WalletPayment(models.Model):
    """
    Audit bridge: links one wallet debit to the school Payment record(s) it
    produced, so a wallet transaction reference can always be traced to
    exactly which students' fees it paid, for which session and term.
    """
    wallet_transaction = models.OneToOneField(
        WalletTransaction,
        on_delete=models.PROTECT,
        related_name='wallet_payment',
    )
    payments = models.ManyToManyField('src.Payment', related_name='wallet_payments')
    session = models.ForeignKey('src.Session', on_delete=models.PROTECT)
    term = models.ForeignKey('src.Term', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"WalletPayment #{self.id} - {self.wallet_transaction.wallet.parent_account} - {self.session} {self.term}"
