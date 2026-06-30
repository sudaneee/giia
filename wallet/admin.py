from django.contrib import admin

from .models import ParentAccount, ParentStudentLink, VirtualAccount, Wallet, WalletPayment, WalletTransaction


class ParentStudentLinkInline(admin.TabularInline):
    model = ParentStudentLink
    extra = 0
    autocomplete_fields = ['student']


@admin.register(ParentAccount)
class ParentAccountAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'user_email', 'phone_number', 'created_at']
    search_fields = ['user__email', 'user__first_name', 'user__last_name', 'phone_number']
    inlines = [ParentStudentLinkInline]

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'Email'


@admin.register(ParentStudentLink)
class ParentStudentLinkAdmin(admin.ModelAdmin):
    list_display = ['parent_account', 'student', 'added_at']
    search_fields = ['parent_account__user__email', 'student__admission_number']


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ['parent_account', 'balance', 'is_active', 'updated_at']
    search_fields = ['parent_account__user__email']
    list_filter = ['is_active']
    readonly_fields = ['balance', 'created_at', 'updated_at']

    def has_add_permission(self, request):
        # Wallets are only ever created automatically via the ParentAccount
        # signal - never by hand, to avoid a wallet existing with no ledger
        # history backing its balance.
        return False


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['wallet', 'transaction_type', 'amount', 'balance_after', 'source', 'status', 'created_at']
    list_filter = ['transaction_type', 'source', 'status']
    search_fields = ['wallet__parent_account__user__email', 'reference']
    readonly_fields = [field.name for field in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VirtualAccount)
class VirtualAccountAdmin(admin.ModelAdmin):
    list_display = ['account_number', 'account_name', 'bank_name', 'wallet', 'created_at']
    search_fields = ['account_number', 'account_name', 'wallet__parent_account__user__email']
    readonly_fields = ['account_number', 'account_name', 'bank_name', 'bank_code', 'wallet', 'created_at']

    def has_add_permission(self, request):
        # Virtual accounts are only ever created via Zainpay through the
        # wallet activation flow - never by hand.
        return False


@admin.register(WalletPayment)
class WalletPaymentAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'session', 'term', 'created_at']
    search_fields = ['wallet_transaction__wallet__parent_account__user__email', 'wallet_transaction__reference']
    list_filter = ['session', 'term']
    readonly_fields = ['wallet_transaction', 'payments', 'session', 'term', 'created_at']

    def has_add_permission(self, request):
        # Only ever created atomically inside pay_school_fees_from_wallet().
        return False
