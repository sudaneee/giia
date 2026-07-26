from django.contrib import admin

from .models import AdmissionOpening, Applicant


@admin.register(AdmissionOpening)
class AdmissionOpeningAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'session', 'capacity', 'paid_applicant_count', 'is_open', 'is_full')
    list_filter = ('session', 'is_open')
    search_fields = ('school_class__name',)


@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = (
        'app_number', 'first_name', 'last_name', 'desired_class', 'session',
        'application_fee_paid', 'status', 'created_at',
    )
    list_filter = ('status', 'application_fee_paid', 'session', 'desired_class')
    search_fields = ('app_number', 'first_name', 'last_name', 'father_name', 'father_phone', 'reference')
    readonly_fields = ('app_number', 'reference', 'amount_paid', 'linked_student', 'created_at')
