from django.contrib import admin
from src.models import *

# Register models using custom admin classes only

admin.site.register(SchoolConfig)
admin.site.register(StudentBehaviouralAssessment)
admin.site.register(Token)
admin.site.register(TahfeezResult)
admin.site.register(MidTermResult)
admin.site.register(PartPaymentApproval)
admin.site.register(PromoCode)

@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'phone_number', 'email', 'relationship')
    search_fields = ('first_name', 'last_name', 'phone_number', 'email')
    list_filter = ('relationship',)

@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ('name', 'level', 'arm')
    search_fields = ('name', 'level')
    list_filter = ('level', 'arm')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('admission_number', 'first_name', 'last_name', 'enrolled_class', 'status')
    search_fields = ('admission_number', 'first_name', 'last_name')
    list_filter = ('status', 'enrolled_class', 'enrollment_date')
    readonly_fields = ('enrollment_date',)

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'current')
    list_filter = ('current',)
    search_fields = ('name',)
    actions = ['make_current']

    def make_current(self, request, queryset):
        queryset.update(current=True)
    make_current.short_description = "Set selected sessions as current"

@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ('name', 'session', 'start_date', 'end_date')
    list_filter = ('session',)
    search_fields = ('name', 'session__name')

@admin.register(Result)
class ResultAdmin(admin.ModelAdmin):
    list_display = ('student', 'subject', 'class_assigned', 'session', 'term', 'total_marks')
    search_fields = ('student__first_name', 'student__last_name', 'subject__name', 'session__name', 'term__name')
    list_filter = ('class_assigned', 'session', 'term')

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'email', 'department', 'role', 'status')
    search_fields = ('first_name', 'last_name', 'email', 'department__name', 'role__name')
    list_filter = ('status', 'department', 'role')

@admin.register(LeaveRecord)
class LeaveRecordAdmin(admin.ModelAdmin):
    list_display = ('staff', 'start_date', 'end_date', 'leave_type', 'status')
    search_fields = ('staff__first_name', 'staff__last_name', 'reason')
    list_filter = ('leave_type', 'status')

@admin.register(StudentAttendanceRecord)
class StudentAttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('student', 'date', 'status', 'session', 'term')
    search_fields = ('student__first_name', 'student__last_name')
    list_filter = ('status', 'session', 'term')

@admin.register(StaffAttendanceRecord)
class StaffAttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('staff', 'date', 'status', 'session', 'term')
    search_fields = ('staff__first_name', 'staff__last_name')
    list_filter = ('status', 'session', 'term')

@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = (
        'section',
        'session',
        'term_group',
        'student_type',
        'transport',
        'total_amount',
    )

    list_filter = (
        'section',
        'session',
        'term_group',
        'student_type',
        'transport',
    )

    search_fields = (
        'section__name',
        'session__name',
    )


@admin.register(FeeComponent)
class FeeComponentAdmin(admin.ModelAdmin):
    list_display = (
        'fee_structure',
        'name',
        'amount',
        'category',
    )

    list_filter = (
        'name',
        'category',
    )

    list_editable = ('category',)

    search_fields = (
        'name',
        'fee_structure__section__name',
    )


@admin.register(OtherFeeStructure)
class OtherFeeStructureAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'category', 'session', 'term', 'active')
    list_filter = ('category', 'active', 'session', 'term')
    list_editable = ('category',)
    search_fields = ('name',)


@admin.register(PaymentBatch)
class PaymentBatchAdmin(admin.ModelAdmin):
    list_display = (
        'reference', 'parent_email', 'amount_paid', 'payment_channel',
        'status', 'webhook_processed', 'webhook_attempts', 'last_webhook_attempt', 'created_at',
    )
    list_filter = ('status', 'webhook_processed', 'payment_channel')
    search_fields = ('reference', 'parent_email')


class PaymentLineItemInline(admin.TabularInline):
    model = PaymentLineItem
    extra = 0
    fields = ('label', 'category', 'amount', 'is_estimated', 'fee_component', 'other_fee')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # Line items are derived from the payment, not hand-entered.
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('student', 'student_class', 'amount_paid', 'payment_method', 'payment_date', 'status', 'session', 'term')
    search_fields = ('student__first_name', 'student__last_name', 'session__name', 'term__name')
    list_filter = ('status', 'payment_method', 'session', 'term', 'line_items__category')
    inlines = [PaymentLineItemInline]

    @admin.display(description='Class', ordering='student__enrolled_class__name')
    def student_class(self, obj):
        return obj.student.enrolled_class or '—'

@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity_in_stock', 'reorder_level')
    search_fields = ('name',)
    list_filter = ('quantity_in_stock',)

@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('item', 'quantity_ordered', 'price_per_unit', 'order_date', 'received_date', 'total_cost', 'supplier')
    search_fields = ('item__name', 'supplier')
    list_filter = ('order_date', 'received_date', 'supplier')
