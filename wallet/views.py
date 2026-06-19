from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from src.models import Session, Student, Term

from .decorators import parent_required
from .forms import AddChildForm, ParentRegistrationForm
from .models import ParentAccount, ParentStudentLink, VirtualAccount, WalletPayment
from .services import fee_service, wallet_service, zainpay_service
from .services.exceptions import (
    InsufficientFundsError,
    VirtualAccountCreationError,
    WalletInactiveError,
    ZainpayTransferError,
)


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
                title=data['title'],
                gender=data['gender'],
                date_of_birth=data['date_of_birth'],
                bvn=data['bvn'],
                address=data['address'],
                state=data['state'],
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
def wallet_overview(request):
    wallet = request.user.parent_account.wallet
    virtual_account = VirtualAccount.objects.filter(wallet=wallet).first()

    return render(request, 'wallet/wallet_overview.html', {
        'wallet': wallet,
        'virtual_account': virtual_account,
    })


@parent_required
@require_POST
def wallet_activate(request):
    parent_account = request.user.parent_account

    try:
        zainpay_service.create_virtual_account(parent_account)
        messages.success(request, 'Your wallet funding account is ready below.')
    except VirtualAccountCreationError as e:
        messages.error(request, f'Could not set up your funding account: {e}')

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


def _linked_student_or_none(parent_account, student_id):
    link = parent_account.student_links.filter(student_id=student_id).select_related('student').first()
    return link.student if link else None


def _build_fee_selections(parent_account, session, term, student_ids):
    """
    Recomputes the outstanding balance for each requested student server-side
    - never trusts amounts submitted by the client - and silently drops any
    student_id that isn't actually linked to this parent.
    """
    fee_selections = []
    for raw_id in student_ids:
        try:
            student_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        student = _linked_student_or_none(parent_account, student_id)
        if not student:
            continue

        result = fee_service.get_default_school_fee_outstanding(student, session, term)
        if result and result['balance'] > 0:
            fee_selections.append({
                'student': student,
                'amount': result['balance'],
                'fee_structure': result['fee_structure'],
            })

    return fee_selections


@parent_required
def outstanding_fees(request):
    parent_account = request.user.parent_account
    sessions = Session.objects.all().order_by('-id')

    session_id = request.GET.get('session_id')
    if not session_id:
        current = sessions.filter(current=True).first()
        session_id = current.id if current else None

    selected_session = Session.objects.filter(id=session_id).first() if session_id else None
    terms = Term.objects.filter(session=selected_session).order_by('id') if selected_session else Term.objects.none()

    term_id = request.GET.get('term_id')
    selected_term = Term.objects.filter(id=term_id, session=selected_session).first() if term_id and selected_session else None

    summaries = []
    if selected_session and selected_term:
        summaries = fee_service.get_guardian_fee_summary(parent_account, selected_session, selected_term)

    return render(request, 'wallet/outstanding_fees.html', {
        'sessions': sessions,
        'terms': terms,
        'selected_session': selected_session,
        'selected_term': selected_term,
        'summaries': summaries,
    })


@parent_required
def pay_fees(request):
    parent_account = request.user.parent_account
    wallet = parent_account.wallet

    session = get_object_or_404(Session, id=request.GET.get('session_id'))
    term = get_object_or_404(Term, id=request.GET.get('term_id'), session=session)
    student_ids = request.GET.getlist('student_id')

    fee_selections = _build_fee_selections(parent_account, session, term, student_ids)
    total_amount = sum(item['amount'] for item in fee_selections)

    estimated_fee = settings.ZAINPAY_TRANSFER_FEE_ESTIMATE
    can_pay_from_wallet = bool(fee_selections) and wallet.balance >= (total_amount + estimated_fee)

    return render(request, 'wallet/pay_fees.html', {
        'session': session,
        'term': term,
        'fee_selections': fee_selections,
        'total_amount': total_amount,
        'wallet': wallet,
        'estimated_fee': estimated_fee,
        'can_pay_from_wallet': can_pay_from_wallet,
        'student_ids': student_ids,
    })


@parent_required
@require_POST
def confirm_wallet_payment(request):
    parent_account = request.user.parent_account

    session = get_object_or_404(Session, id=request.POST.get('session_id'))
    term = get_object_or_404(Term, id=request.POST.get('term_id'), session=session)
    student_ids = request.POST.getlist('student_id')

    fee_selections = _build_fee_selections(parent_account, session, term, student_ids)

    if not fee_selections:
        messages.error(request, 'Nothing to pay.')
        return redirect('wallet:outstanding_fees')

    try:
        wallet_payment = wallet_service.pay_school_fees_from_wallet(parent_account, fee_selections, session, term)
        messages.success(request, 'Payment successful!')
        return redirect('wallet:receipt_detail', reference=wallet_payment.wallet_transaction.reference)
    except InsufficientFundsError:
        messages.error(request, 'Insufficient wallet balance for this payment.')
    except (ZainpayTransferError, WalletInactiveError, ValueError) as e:
        messages.error(request, f'Payment could not be completed: {e}')

    return redirect('wallet:outstanding_fees')


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
    payments = wallet_payment.payments.select_related('student', 'fee_structure').all()

    return render(request, 'wallet/receipt_detail.html', {
        'wallet_payment': wallet_payment,
        'payments': payments,
    })
