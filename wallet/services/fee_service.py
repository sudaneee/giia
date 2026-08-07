from decimal import Decimal

from django.db.models import Sum

from src.models import FeeStructure, FeeWaiverApproval, OtherFeeStructure, Payment


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


def is_tahfeez_fee(other_fee):
    """
    Identifies the Tahfeez fee among OtherFeeStructure rows so it can be
    split into its own payment tab everywhere fees are paid, instead of
    sitting in the general Other Fees list. Matched by name (case-insensitive
    substring) rather than a dedicated flag, so renaming the fee to something
    without "tahfeez" in it will silently drop it back into Other Fees.
    """
    return 'tahfeez' in (other_fee.name or '').lower()


def is_transportation_fee(other_fee):
    """
    Identifies the Transportation fee among OtherFeeStructure rows (e.g.
    "TRANSPORTATION") the same way is_tahfeez_fee does - by name. Pulled out
    of the general Other Fees list because it's offered as a With/No
    Transportation toggle on the Tahfeez tab instead.
    """
    return 'transport' in (other_fee.name or '').lower()


def split_special_other_fees(other_fee_structures):
    """
    Splits an iterable/queryset of OtherFeeStructure rows into
    (tahfeez_fees, transportation_fees, other_fees), preserving order.
    Tahfeez and Transportation are surfaced on their own tab/toggle
    everywhere fees are paid, so neither belongs in the general Other Fees
    list.
    """
    tahfeez_fees = []
    transportation_fees = []
    other_fees = []
    for fee in other_fee_structures:
        if is_tahfeez_fee(fee):
            tahfeez_fees.append(fee)
        elif is_transportation_fee(fee):
            transportation_fees.append(fee)
        else:
            other_fees.append(fee)
    return tahfeez_fees, transportation_fees, other_fees


def resolve_tahfeez_fee_ids(session, term, transport):
    """
    The Tahfeez tab doesn't make the payer pick fees by checkbox - it
    automatically charges every active Tahfeez fee for the given
    session/term, plus every active Transportation fee when the payer opts
    into transport via the With/No Transportation toggle.
    """
    term_other_fees = OtherFeeStructure.objects.filter(active=True, session=session, term=term)
    tahfeez_fees, transportation_fees, _ = split_special_other_fees(term_other_fees)
    fee_ids = [fee.id for fee in tahfeez_fees]
    if transport:
        fee_ids += [fee.id for fee in transportation_fees]
    return fee_ids


def is_valid_staff_promo_code(code):
    """Checks code against active PromoCode rows. Blank/None is never valid."""
    from src.models import PromoCode

    code = (code or '').strip()
    if not code:
        return False
    return PromoCode.objects.filter(code__iexact=code, active=True).exists()


def calculate_other_fee_outstanding(student, other_fee, session, term, child_index=0, staff_discount_applies=False):
    """
    Computes one student's amount due for a single OtherFeeStructure. The
    existing other-fees flow charges the flat fee amount with no deduction
    for prior payments.

    child_index is this student's position (0-based) among the children the
    parent is paying this same fee for in one payment. When the fee has a
    multi_child_discount_percent set, the first child (index 0) pays full
    price and every later child gets that percentage off.

    staff_discount_applies is True when the payer supplied a valid active
    PromoCode. It overrides (does not stack with) the multi-child discount:
    every child, including the first, gets other_fee.staff_discount_percent off.
    """
    amount = other_fee.amount
    if staff_discount_applies and other_fee.staff_discount_percent:
        discount = other_fee.staff_discount_percent / Decimal('100')
        amount = (amount * (Decimal('1') - discount)).quantize(Decimal('0.01'))
    elif child_index > 0 and other_fee.multi_child_discount_percent:
        discount = other_fee.multi_child_discount_percent / Decimal('100')
        amount = (amount * (Decimal('1') - discount)).quantize(Decimal('0.01'))

    return {
        'fee_id': other_fee.id,
        'name': other_fee.name,
        'amount': amount,
    }
