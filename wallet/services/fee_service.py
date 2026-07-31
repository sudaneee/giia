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


def calculate_other_fee_outstanding(student, other_fee, session, term, child_index=0):
    """
    Computes one student's amount due for a single OtherFeeStructure. The
    existing other-fees flow charges the flat fee amount with no deduction
    for prior payments.

    child_index is this student's position (0-based) among the children the
    parent is paying this same fee for in one payment. When the fee has a
    multi_child_discount_percent set, the first child (index 0) pays full
    price and every later child gets that percentage off.
    """
    amount = other_fee.amount
    if child_index > 0 and other_fee.multi_child_discount_percent:
        discount = other_fee.multi_child_discount_percent / Decimal('100')
        amount = (amount * (Decimal('1') - discount)).quantize(Decimal('0.01'))

    return {
        'fee_id': other_fee.id,
        'name': other_fee.name,
        'amount': amount,
    }
