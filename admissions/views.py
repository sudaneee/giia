import logging
from datetime import datetime
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from src.models import Session
from wallet.services import zainpay_service
from wallet.services.exceptions import ZainpayCheckoutError

from .models import AdmissionOpening, Applicant
from .services import confirm_application_payment

logger = logging.getLogger(__name__)


def _current_session():
    return Session.objects.filter(current=True).first()


def _open_admission_openings(session):
    if not session:
        return []
    openings = AdmissionOpening.objects.filter(session=session).select_related(
        'school_class', 'school_class__section',
    )
    return [o for o in openings if o.is_available]


def _validate_application(request, matching_opening):
    errors = []

    if not matching_opening:
        errors.append('Please select a valid, currently open class.')

    required_text_fields = {
        'first_name': "Pupil's first name",
        'last_name': "Pupil's last name",
        'gender': "Pupil's gender",
        'residential_address': 'Residential address',
        'father_name': "Father/guardian's name",
        'father_phone': "Father/guardian's phone number",
        'declaration_name': 'Declaration name',
    }
    for field, label in required_text_fields.items():
        if not request.POST.get(field, '').strip():
            errors.append(f'{label} is required.')

    dob_raw = request.POST.get('date_of_birth', '').strip()
    dob = None
    if not dob_raw:
        errors.append('Date of birth is required.')
    else:
        try:
            dob = datetime.strptime(dob_raw, '%Y-%m-%d').date()
        except ValueError:
            errors.append('Date of birth is not a valid date.')

    if not request.POST.get('declaration_agreed'):
        errors.append('You must agree to the declaration to submit.')

    return errors, dob


def apply(request):
    session = _current_session()
    openings = _open_admission_openings(session)
    normal_openings = [o for o in openings if o.school_class.section.name != 'TAHFEEZ']
    tahfeez_openings = [o for o in openings if o.school_class.section.name == 'TAHFEEZ']

    context = {
        'normal_openings': normal_openings,
        'tahfeez_openings': tahfeez_openings,
        'application_fee': settings.APPLICATION_FEE_AMOUNT,
    }

    if request.method == 'POST':
        if not session:
            messages.error(request, 'Applications are not currently open.')
            return render(request, 'admissions/apply.html', context)

        desired_class_id = request.POST.get('desired_class')
        matching_opening = next(
            (o for o in openings if str(o.school_class_id) == desired_class_id), None,
        )

        errors, dob = _validate_application(request, matching_opening)
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'admissions/apply.html', context)

        reference = f"APPFEE-{uuid4().hex[:12].upper()}"

        applicant = Applicant.objects.create(
            first_name=request.POST.get('first_name', '').strip(),
            last_name=request.POST.get('last_name', '').strip(),
            date_of_birth=dob,
            place_of_birth=request.POST.get('place_of_birth', '').strip(),
            gender=request.POST.get('gender', ''),
            native_language=request.POST.get('native_language', '').strip(),
            blood_group=request.POST.get('blood_group', ''),
            genotype=request.POST.get('genotype', ''),
            has_ailment=request.POST.get('has_ailment') == 'yes',
            ailment_details=request.POST.get('ailment_details', '').strip(),
            residential_address=request.POST.get('residential_address', '').strip(),
            photo=request.FILES.get('photo'),
            father_name=request.POST.get('father_name', '').strip(),
            father_address=request.POST.get('father_address', '').strip(),
            father_occupation=request.POST.get('father_occupation', '').strip(),
            father_qualification=request.POST.get('father_qualification', '').strip(),
            father_phone=request.POST.get('father_phone', '').strip(),
            father_email=request.POST.get('father_email', '').strip(),
            mother_name=request.POST.get('mother_name', '').strip(),
            mother_qualification=request.POST.get('mother_qualification', '').strip(),
            mother_phone=request.POST.get('mother_phone', '').strip(),
            mother_email=request.POST.get('mother_email', '').strip(),
            session=session,
            desired_class=matching_opening.school_class,
            declaration_name=request.POST.get('declaration_name', '').strip(),
            declaration_agreed=True,
            reference=reference,
        )

        try:
            redirect_url = zainpay_service.initialize_checkout(
                email=applicant.father_email or applicant.mother_email or 'no-reply@greatinsight.example',
                mobile_number=applicant.father_phone or applicant.mother_phone,
                amount=settings.APPLICATION_FEE_AMOUNT,
                txn_ref=reference,
                callback_url=request.build_absolute_uri(reverse('admissions:apply_callback')),
                zainbox_code=settings.ZAINPAY_SCHOOL_ZAINBOX_CODE,
            )
        except ZainpayCheckoutError as e:
            messages.error(request, f'Could not start application payment: {e}')
            return render(request, 'admissions/apply.html', context)

        return redirect(redirect_url)

    return render(request, 'admissions/apply.html', context)


@csrf_exempt
def apply_callback(request):
    # Deliberately not gated on auth/CSRF - this is a fully public flow, and
    # Zainpay may hit this server-to-server with no browser session at all
    # (confirmed behavior for the wallet-funding checkout callback - see
    # wallet_fund_callback). Logged unconditionally, before anything else.
    logger.info(
        'admissions apply_callback hit: method=%s GET=%s raw_body=%r',
        request.method, dict(request.GET), request.body[:1000],
    )

    txn_ref = request.GET.get('txnRef')
    status = (request.GET.get('status') or '').lower()

    if not txn_ref:
        messages.success(request, "Application received! We'll confirm your payment shortly.")
        return redirect('admissions:apply')

    if status in ('cancel', 'cancelled', 'failed', 'failure'):
        messages.error(request, 'Payment was not completed - your application was not submitted.')
        return redirect('admissions:apply')

    if confirm_application_payment(txn_ref):
        applicant = Applicant.objects.filter(reference=txn_ref).first()
        app_number = applicant.app_number if applicant else ''
        messages.success(request, f'Application submitted successfully! Your application number is {app_number}.')
    else:
        messages.success(request, "Payment received! We'll confirm and finalize your application shortly.")

    return redirect('admissions:apply')
