import logging
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from src.models import OtherFeeStructure, Session, Student, Term

from .decorators import parent_required
from .forms import AddChildForm, ParentRegistrationForm
from .models import ParentAccount, ParentStudentLink, WalletFundingRequest, WalletPayment
from .services import fee_service, paystack_service, wallet_service, zainpay_service
from .services.exceptions import (
    InsufficientFundsError,
    WalletInactiveError,
    ZainpayCheckoutError,
    ZainpayTransferError,
)
from .services.wallet_service import credit_wallet

logger = logging.getLogger(__name__)


def register(request):
    if request.user.is_authenticated and hasattr(request.user, 'parent_account'):
        return redirect('wallet:dashboard')

    if request.method == 'POST':
        form = ParentRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            user = User.objects.create_user(
                username=data['email'],
                email=data['email'],
                password=data['password1'],
                first_name=data['first_name'],
                last_name=data['last_name'],
            )
            ParentAccount.objects.create(
                user=user,
                phone_number=data['phone_number'],
            )
            login(request, user)
            messages.success(request, 'Registration successful. Welcome to the parent portal!')
            return redirect('wallet:dashboard')
    else:
        form = ParentRegistrationForm()

    return render(request, 'wallet/register.html', {'form': form})


def parent_login(request):
    if request.user.is_authenticated and hasattr(request.user, 'parent_account'):
        return redirect('wallet:dashboard')

    error = None
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        password = request.POST.get('password', '')

        username = identifier
        if '@' in identifier:
            user_obj = User.objects.filter(email__iexact=identifier).first()
            if user_obj:
                username = user_obj.username

        user = authenticate(request, username=username, password=password)

        if user is not None and hasattr(user, 'parent_account'):
            login(request, user)
            next_url = request.GET.get('next') or reverse('wallet:dashboard')
            return redirect(next_url)
        elif user is not None:
            error = 'This login is for parents only.'
        else:
            error = 'Invalid email/username or password.'

    return render(request, 'wallet/login.html', {'error': error})


def parent_logout(request):
    logout(request)
    return redirect('wallet:login')


@parent_required
def dashboard(request):
    parent_account = request.user.parent_account
    wallet = parent_account.wallet
    links = parent_account.student_links.select_related('student', 'student__enrolled_class').all()
    recent_transactions = wallet.transactions.all()[:5]

    return render(request, 'wallet/dashboard.html', {
        'wallet': wallet,
        'links': links,
        'recent_transactions': recent_transactions,
    })


@parent_required
def children_list(request):
    parent_account = request.user.parent_account
    links = ParentStudentLink.objects.filter(
        parent_account=parent_account
    ).select_related('student', 'student__enrolled_class')

    return render(request, 'wallet/children_list.html', {'links': links})


@parent_required
def add_child(request):
    parent_account = request.user.parent_account

    if request.method == 'POST':
        form = AddChildForm(request.POST)
        if form.is_valid():
            admission_number = form.cleaned_data['admission_number'].strip()
            student = Student.objects.filter(admission_number=admission_number).first()

            if not student:
                form.add_error('admission_number', f'No student found with admission number "{admission_number}".')
            elif ParentStudentLink.objects.filter(parent_account=parent_account, student=student).exists():
                form.add_error('admission_number', f'{student.first_name} {student.last_name} is already linked to your account.')
            else:
                ParentStudentLink.objects.create(parent_account=parent_account, student=student)
                messages.success(request, f'{student.first_name} {student.last_name} has been added to your account.')
                return redirect('wallet:children_list')
    else:
        form = AddChildForm()

    return render(request, 'wallet/add_child.html', {'form': form})


@parent_required
@require_POST
def remove_child(request, link_id):
    parent_account = request.user.parent_account
    link = get_object_or_404(ParentStudentLink, id=link_id, parent_account=parent_account)
    name = f"{link.student.first_name} {link.student.last_name}"
    link.delete()
    messages.success(request, f'{name} has been removed from your account.')
    return redirect('wallet:children_list')


@parent_required
def wallet_overview(request):
    wallet = request.user.parent_account.wallet

    return render(request, 'wallet/wallet_overview.html', {
        'wallet': wallet,
        'funding_provider': settings.WALLET_FUNDING_PROVIDER,
    })


@parent_required
@require_POST
def wallet_fund_initiate(request):
    parent_account = request.user.parent_account
    wallet = parent_account.wallet

    if not wallet.is_active:
        messages.error(request, 'This wallet is not active.')
        return redirect('wallet:wallet_overview')

    try:
        amount = Decimal(request.POST.get('amount', '0'))
    except Exception:
        amount = Decimal('0')

    if amount <= 0:
        messages.error(request, 'Enter a valid amount to fund.')
        return redirect('wallet:wallet_overview')

    if settings.WALLET_FUNDING_PROVIDER == 'zainpay':
        reference = f"WALLETFUND-{uuid4().hex[:12].upper()}"
        WalletFundingRequest.objects.create(wallet=wallet, reference=reference, amount=amount)

        try:
            redirect_url = zainpay_service.initialize_checkout(
                email=request.user.email,
                mobile_number=parent_account.phone_number,
                amount=amount,
                txn_ref=reference,
                callback_url=request.build_absolute_uri(reverse('wallet:wallet_fund_callback')),
                zainbox_code=settings.ZAINPAY_WALLET_ZAINBOX_CODE,
            )
        except ZainpayCheckoutError as e:
            messages.error(request, f'Could not start wallet funding: {e}')
            return redirect('wallet:wallet_overview')

        return redirect(redirect_url)

    # Paystack: payment_method choice only matters here, for its fee calc.
    payment_method = request.POST.get('payment_method', 'card')
    if payment_method not in ('card', 'bank_transfer'):
        payment_method = 'card'

    fee = paystack_service.calculate_topup_fee(amount, payment_method)
    total_charge = amount + fee
    reference = f"WALLETFUND-{uuid4().hex[:12].upper()}"

    result = paystack_service.initialize_topup(
        email=request.user.email,
        amount=total_charge,
        payment_method=payment_method,
        reference=reference,
        callback_url=request.build_absolute_uri(reverse('wallet:wallet_fund_callback')),
        metadata={
            'purpose': 'wallet_funding',
            'wallet_id': wallet.id,
            'requested_amount': str(amount),
        },
    )

    if result.get('status') and result.get('data', {}).get('authorization_url'):
        return redirect(result['data']['authorization_url'])

    messages.error(request, 'Could not start wallet funding. Please try again.')
    return redirect('wallet:wallet_overview')


@csrf_exempt
def wallet_fund_callback(request):
    # Deliberately NOT @parent_required, and exempted from CSRF: some
    # gateways (possibly Zainpay - under investigation) may hit this URL
    # server-to-server, with no browser session or CSRF token at all, in
    # addition to the browser redirect a logged-in parent gets sent through.
    # Gating this on auth/CSRF would silently reject any such server call
    # before we ever saw it. Logged unconditionally, before anything else, so
    # we always have a record of what actually arrived here regardless of
    # method/auth state.
    logger.info(
        'wallet_fund_callback hit: method=%s GET=%s POST=%s raw_body=%r authenticated=%s',
        request.method, dict(request.GET), dict(request.POST), request.body[:1000], request.user.is_authenticated,
    )

    if settings.WALLET_FUNDING_PROVIDER == 'zainpay':
        # Confirmed in production: Zainpay appends status/txnRef as query
        # params on the browser redirect. There's still no documented
        # verify-by-reference endpoint for this product, so rather than
        # trust the "status" value blindly, use it to look up the deposit
        # directly in Zainpay's own transaction history and credit
        # immediately if it's really there - idempotent with the webhook and
        # sync_zainpay_transactions either way.
        txn_ref = request.GET.get('txnRef')
        status = (request.GET.get('status') or '').lower()

        if not txn_ref:
            messages.success(request, "Payment received! Your wallet will update within a few minutes once we confirm with Zainpay.")
            return redirect('wallet:wallet_overview')

        if status in ('cancel', 'cancelled', 'failed', 'failure'):
            messages.error(request, 'Payment was not completed.')
            return redirect('wallet:wallet_overview')

        if wallet_service.credit_zainpay_deposit_if_found(txn_ref):
            messages.success(request, 'Your wallet has been funded successfully.')
        else:
            messages.success(request, "Payment received! Your wallet will update within a few minutes once we confirm with Zainpay.")
        return redirect('wallet:wallet_overview')

    reference = request.GET.get('reference')
    if not reference:
        messages.error(request, 'No payment reference provided.')
        return redirect('wallet:wallet_overview')

    verified = paystack_service.verify_transaction(reference)
    if not verified:
        messages.error(request, 'Payment could not be verified.')
        return redirect('wallet:wallet_overview')

    metadata = verified.get('metadata') or {}
    if metadata.get('purpose') != 'wallet_funding':
        messages.error(request, 'Payment could not be verified.')
        return redirect('wallet:wallet_overview')

    credit_wallet(
        wallet_id=metadata['wallet_id'],
        amount=metadata['requested_amount'],
        reference=reference,
        narration='Wallet funding via Paystack',
        source='paystack_callback',
        metadata=verified,
    )
    messages.success(request, 'Your wallet has been funded successfully.')
    return redirect('wallet:wallet_overview')


@parent_required
def wallet_transactions(request):
    wallet = request.user.parent_account.wallet
    qs = wallet.transactions.all()

    type_filter = request.GET.get('type', '')
    if type_filter in ('credit', 'debit'):
        qs = qs.filter(transaction_type=type_filter)

    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'wallet/wallet_transactions.html', {
        'page_obj': page_obj,
        'type_filter': type_filter,
        'date_from': date_from,
        'date_to': date_to,
    })


VALID_STUDENT_TYPES = ('new', 'returning')


def _linked_student_or_none(parent_account, student_id):
    link = parent_account.student_links.filter(student_id=student_id).select_related('student', 'student__enrolled_class').first()
    return link.student if link else None


def _safe_int_list(raw_ids):
    result = []
    for raw_id in raw_ids:
        try:
            result.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    return result


def _clean_student_type(request):
    student_type = request.GET.get('student_type') or request.POST.get('student_type', 'returning')
    return student_type if student_type in VALID_STUDENT_TYPES else 'returning'


def _clean_transport(request):
    raw = request.GET.get('transport') or request.POST.get('transport', 'false')
    return raw == 'true'


def _build_school_fee_selections(parent_account, session, term, student_ids, student_type, transport):
    """
    Recomputes each student's outstanding balance server-side for the given
    student_type/transport choice - matches the existing Paystack flow, where
    one student_type/transport selection applies to the whole batch. Never
    trusts amounts submitted by the client, and silently drops any
    student_id that isn't actually linked to this parent.
    """
    term_group = term.name.lower().split()[0]
    fee_selections = []

    for student_id in _safe_int_list(student_ids):
        student = _linked_student_or_none(parent_account, student_id)
        if not student:
            continue

        result = fee_service.calculate_school_fee_outstanding(
            student, session, term, term_group, student_type, transport,
        )
        if result and result['balance'] > 0:
            fee_selections.append({
                'student': student,
                'amount': result['balance'],
                'fee_structure': result['fee_structure'],
            })

    return fee_selections


def _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids, promo_code=None):
    """
    One fee_selections entry per (student, other_fee) pair - e.g. selecting
    two children and two fees produces four entries. Drops any fee_id that
    isn't active, isn't scoped to the chosen session/term, or belongs to a
    student not linked to this parent.
    """
    other_fees = OtherFeeStructure.objects.filter(
        id__in=_safe_int_list(fee_ids), active=True, session=session, term=term,
    )
    staff_discount_applies = fee_service.is_valid_staff_promo_code(promo_code)

    fee_selections = []
    child_index_by_fee = {}
    for student_id in _safe_int_list(student_ids):
        student = _linked_student_or_none(parent_account, student_id)
        if not student:
            continue

        for other_fee in other_fees:
            child_index = child_index_by_fee.get(other_fee.id, 0)
            child_index_by_fee[other_fee.id] = child_index + 1
            result = fee_service.calculate_other_fee_outstanding(
                student, other_fee, session, term, child_index, staff_discount_applies,
            )
            fee_selections.append({
                'student': student,
                'amount': result['amount'],
                'other_fee': other_fee,
            })

    return fee_selections


@parent_required
def make_payment(request):
    """
    Entry point for paying fees, mirroring the existing Paystack
    unified_payment flow: the parent picks which children to pay for and the
    payment parameters (student type/transport for school fees, or which
    fees for other fees) *before* seeing any breakdown - the breakdown and
    payment method choice happen on the next page (pay_fees), computed only
    for what was actually selected here.
    """
    parent_account = request.user.parent_account
    sessions = Session.objects.all().order_by('-id')

    fee_type = request.GET.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'other_fees'):
        fee_type = 'school_fees'

    session_id = request.GET.get('session_id')
    if not session_id:
        current = sessions.filter(current=True).first()
        session_id = current.id if current else None

    selected_session = Session.objects.filter(id=session_id).first() if session_id else None
    terms = Term.objects.filter(session=selected_session).order_by('id') if selected_session else Term.objects.none()

    term_id = request.GET.get('term_id')
    selected_term = Term.objects.filter(id=term_id, session=selected_session).first() if term_id and selected_session else None

    student_type = _clean_student_type(request)
    transport = _clean_transport(request)

    links = parent_account.student_links.select_related('student', 'student__enrolled_class').all()
    if fee_type == 'other_fees' and selected_session and selected_term:
        other_fee_structures = OtherFeeStructure.objects.filter(
            active=True, session=selected_session, term=selected_term,
        )
    else:
        other_fee_structures = OtherFeeStructure.objects.none()

    return render(request, 'wallet/make_payment.html', {
        'sessions': sessions,
        'terms': terms,
        'selected_session': selected_session,
        'selected_term': selected_term,
        'fee_type': fee_type,
        'student_type': student_type,
        'transport': transport,
        'other_fee_structures': other_fee_structures,
        'links': links,
    })


@parent_required
def pay_fees(request):
    parent_account = request.user.parent_account
    wallet = parent_account.wallet

    fee_type = request.GET.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'other_fees'):
        fee_type = 'school_fees'

    session = get_object_or_404(Session, id=request.GET.get('session_id'))
    term = get_object_or_404(Term, id=request.GET.get('term_id'), session=session)
    student_ids = request.GET.getlist('student_id')

    student_type = _clean_student_type(request)
    transport = _clean_transport(request)

    promo_code = request.GET.get('promo_code', '').strip()

    if fee_type == 'school_fees':
        fee_selections = _build_school_fee_selections(
            parent_account, session, term, student_ids, student_type, transport,
        )
    else:
        fee_ids = request.GET.getlist('fee_id')
        fee_selections = _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids, promo_code)

    total_amount = sum(item['amount'] for item in fee_selections)

    # Zainpay-funded wallets pay a real transfer fee on top of the fee amount
    # when spending from the wallet (see pay_school_fees_from_wallet) - this
    # is just the matching UX pre-check so the button isn't shown as enabled
    # only to fail with InsufficientFundsError on click.
    required_balance = total_amount
    if settings.WALLET_FUNDING_PROVIDER == 'zainpay' and settings.ZAINPAY_LIVE_TRANSFER_ENABLED:
        required_balance += settings.ZAINPAY_TRANSFER_FEE_ESTIMATE
    can_pay_from_wallet = bool(fee_selections) and wallet.balance >= required_balance

    # Deduplicated student ids for the confirm form's hidden inputs - other
    # fees can produce several fee_selections entries for the same student
    # (one per fee), so looping fee_selections directly would submit that
    # student_id more than once and double-process them downstream.
    unique_student_ids = list(dict.fromkeys(item['student'].id for item in fee_selections))

    return render(request, 'wallet/pay_fees.html', {
        'session': session,
        'term': term,
        'fee_type': fee_type,
        'student_type': student_type,
        'transport': transport,
        'fee_selections': fee_selections,
        'unique_student_ids': unique_student_ids,
        'total_amount': total_amount,
        'wallet': wallet,
        'can_pay_from_wallet': can_pay_from_wallet,
        'student_ids': student_ids,
        'fee_ids': request.GET.getlist('fee_id') if fee_type == 'other_fees' else [],
        'promo_code': promo_code,
        'staff_discount_applied': fee_type == 'other_fees' and fee_service.is_valid_staff_promo_code(promo_code),
    })


@parent_required
@require_POST
def confirm_wallet_payment(request):
    parent_account = request.user.parent_account

    fee_type = request.POST.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'other_fees'):
        fee_type = 'school_fees'

    session = get_object_or_404(Session, id=request.POST.get('session_id'))
    term = get_object_or_404(Term, id=request.POST.get('term_id'), session=session)
    student_ids = request.POST.getlist('student_id')

    if fee_type == 'school_fees':
        student_type = _clean_student_type(request)
        transport = _clean_transport(request)
        fee_selections = _build_school_fee_selections(
            parent_account, session, term, student_ids, student_type, transport,
        )
    else:
        fee_ids = request.POST.getlist('fee_id')
        promo_code = request.POST.get('promo_code', '').strip()
        fee_selections = _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids, promo_code)

    if not fee_selections:
        messages.error(request, 'Nothing to pay.')
        return redirect('wallet:make_payment')

    try:
        wallet_payment = wallet_service.pay_school_fees_from_wallet(parent_account, fee_selections, session, term)
        messages.success(request, 'Payment successful!')
        return redirect('wallet:receipt_detail', reference=wallet_payment.wallet_transaction.reference)
    except InsufficientFundsError:
        messages.error(request, 'Insufficient wallet balance for this payment.')
    except (ZainpayTransferError, WalletInactiveError, ValueError) as e:
        messages.error(request, f'Payment could not be completed: {e}')

    return redirect('wallet:make_payment')


@parent_required
def receipt_list(request):
    parent_account = request.user.parent_account
    wallet_payments = WalletPayment.objects.filter(
        wallet_transaction__wallet=parent_account.wallet
    ).select_related('wallet_transaction', 'session', 'term').order_by('-created_at')

    return render(request, 'wallet/receipt_list.html', {'wallet_payments': wallet_payments})


@parent_required
def receipt_detail(request, reference):
    parent_account = request.user.parent_account
    wallet_payment = get_object_or_404(
        WalletPayment,
        wallet_transaction__reference=reference,
        wallet_transaction__wallet=parent_account.wallet,
    )
    payments = wallet_payment.payments.select_related('student', 'student__enrolled_class', 'fee_structure', 'other_fee').all()

    return render(request, 'wallet/receipt_detail.html', {
        'wallet_payment': wallet_payment,
        'payments': payments,
    })
