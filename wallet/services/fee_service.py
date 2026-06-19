from decimal import Decimal

from django.db.models import Sum

from src.models import FeeStructure, FeeWaiverApproval, Payment


def _compute_outstanding_for_fee_structure(student, fee, session, term):
    total_paid = Payment.objects.filter(
        student=student,
        fee_structure=fee,
        session=session,
        term=term,
    ).exclude(payment_method='waiver').aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')

    waiver = FeeWaiverApproval.objects.filter(
        student=student,
        session=session,
        term=term,
        status='active',
    ).first()

    waiver_percentage = waiver.waiver_percentage if waiver else 0

    tuition_component = fee.components.filter(name__iexact='tuition').first()
    tuition_amount = tuition_component.amount if tuition_component else Decimal('0.00')
    waived_amount = (tuition_amount * Decimal(waiver_percentage) / Decimal('100')).quantize(Decimal('0.01'))

    raw_balance = fee.total_amount - total_paid
    net_balance = max(raw_balance - waived_amount, Decimal('0.00'))

    return {
        'fee_structure': fee,
        'total_fee': fee.total_amount,
        'paid': total_paid,
        'waiver_percentage': waiver_percentage,
        'waived_amount': waived_amount,
        'balance': net_balance,
    }


def calculate_school_fee_outstanding(student, session, term, term_group, student_type='returning', transport=False):
    """
    Computes one student's outstanding balance for a school FeeStructure
    (gross amount, prior payments, active waiver). Mirrors the per-student
    logic used by the Paystack payment flow, where the parent explicitly
    picks student_type/transport on the payment form. Returns None if the
    student has no class/section assigned or no matching FeeStructure exists.
    """
    if not student.enrolled_class or not student.enrolled_class.section:
        return None

    fee = FeeStructure.objects.filter(
        section=student.enrolled_class.section,
        session=session,
        term_group=term_group,
        student_type=student_type,
        transport=transport,
    ).first()

    if not fee:
        return None

    return _compute_outstanding_for_fee_structure(student, fee, session, term)


def get_default_school_fee_outstanding(student, session, term):
    """
    Same calculation as calculate_school_fee_outstanding(), but for contexts
    with no payment form to ask the parent for student_type/transport (the
    wallet's Outstanding Fees page). term_group and student_type are derived
    automatically, matching the existing pattern already used by
    class_fee_compliance() and student_payment_status_report() in
    src/views.py. Does not filter by transport, matching that same precedent.
    """
    if not student.enrolled_class or not student.enrolled_class.section:
        return None

    term_group = term.name.lower().split()[0]
    student_type = 'new' if student.admission_status == 'admitted' else 'returning'

    fee = FeeStructure.objects.filter(
        section=student.enrolled_class.section,
        session=session,
        term_group=term_group,
        student_type=student_type,
    ).first()

    if not fee:
        return None

    return _compute_outstanding_for_fee_structure(student, fee, session, term)


def get_guardian_fee_summary(parent_account, session, term):
    """
    Per-child outstanding summary for every student linked to this parent,
    for the given session/term. Used by the wallet's Outstanding Fees page.
    Returns a list of {'student': Student, 'result': dict|None} entries -
    result is None when no fee structure applies to that student.
    """
    summaries = []
    links = parent_account.student_links.select_related(
        'student', 'student__enrolled_class'
    ).all()

    for link in links:
        result = get_default_school_fee_outstanding(link.student, session, term)
        summaries.append({'student': link.student, 'result': result})

    return summaries


def calculate_other_fee_outstanding(student, other_fee, session, term):
    """
    Computes one student's amount due for a single OtherFeeStructure. The
    existing other-fees flow charges the flat fee amount with no deduction
    for prior payments, so this is currently a direct passthrough.
    """
    return {
        'fee_id': other_fee.id,
        'name': other_fee.name,
        'amount': other_fee.amount,
    }
