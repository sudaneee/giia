from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from src.models import OtherFeeStructure, Session, Student, Term
from wallet.forms import AddChildForm, ParentRegistrationForm
from wallet.models import ParentAccount, ParentStudentLink, VirtualAccount, WalletPayment
from wallet.services import fee_service, wallet_service, zainpay_service
from wallet.services.exceptions import (
    InsufficientFundsError,
    VirtualAccountCreationError,
    WalletInactiveError,
    ZainpayTransferError,
)
from django.conf import settings


def superadmin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not request.user.is_superuser:
            messages.error(request, 'Access denied. Superadmin only.')
            return redirect('student_list')
        return view_func(request, *args, **kwargs)
    return wrapper


def _linked_student_or_none(parent_account, student_id):
    link = parent_account.student_links.filter(
        student_id=student_id
    ).select_related('student', 'student__enrolled_class').first()
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
    st = request.GET.get('student_type') or request.POST.get('student_type', 'returning')
    return st if st in ('new', 'returning') else 'returning'


def _clean_transport(request):
    raw = request.GET.get('transport') or request.POST.get('transport', 'false')
    return raw == 'true'


def _build_school_fee_selections(parent_account, session, term, student_ids, student_type, transport):
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


def _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids):
    other_fees = OtherFeeStructure.objects.filter(id__in=_safe_int_list(fee_ids), active=True)
    fee_selections = []
    for student_id in _safe_int_list(student_ids):
        student = _linked_student_or_none(parent_account, student_id)
        if not student:
            continue
        for other_fee in other_fees:
            result = fee_service.calculate_other_fee_outstanding(student, other_fee, session, term)
            fee_selections.append({
                'student': student,
                'amount': result['amount'],
                'other_fee': other_fee,
            })
    return fee_selections


@superadmin_required
def wallet_admin_list(request):
    parent_accounts = ParentAccount.objects.select_related(
        'user', 'wallet', 'wallet__virtual_account'
    ).prefetch_related('student_links').order_by('user__last_name', 'user__first_name')
    return render(request, 'src/wallet_admin_list.html', {'parent_accounts': parent_accounts})


@superadmin_required
def wallet_admin_create(request):
    if request.method == 'POST':
        form = ParentRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            if User.objects.filter(email__iexact=data['email']).exists():
                form.add_error('email', 'An account with this email already exists.')
            else:
                user = User.objects.create_user(
                    username=data['email'],
                    email=data['email'],
                    password=data['password1'],
                    first_name=data['first_name'],
                    last_name=data['last_name'],
                )
                ParentAccount.objects.create(user=user, phone_number=data['phone_number'])
                messages.success(request, f'Wallet account created for {user.get_full_name()}.')
                return redirect('wallet_admin_list')
    else:
        form = ParentRegistrationForm()
    return render(request, 'src/wallet_admin_create.html', {'form': form})


@superadmin_required
def wallet_admin_detail(request, parent_id):
    from datetime import date, timedelta
    parent_account = get_object_or_404(ParentAccount.objects.select_related('user', 'wallet'), id=parent_id)
    wallet = parent_account.wallet
    virtual_account = VirtualAccount.objects.filter(wallet=wallet).first()
    links = parent_account.student_links.select_related('student', 'student__enrolled_class').all()
    add_child_form = AddChildForm()

    qs = wallet.transactions.order_by('-created_at')

    default_from = (date.today() - timedelta(days=30)).isoformat()
    date_from = request.GET.get('date_from', default_from)
    date_to   = request.GET.get('date_to', '')
    txn_type  = request.GET.get('txn_type', '')

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if txn_type in ('credit', 'debit'):
        qs = qs.filter(transaction_type=txn_type)

    return render(request, 'src/wallet_admin_detail.html', {
        'parent_account': parent_account,
        'wallet': wallet,
        'virtual_account': virtual_account,
        'links': links,
        'transactions': qs,
        'add_child_form': add_child_form,
        'date_from': date_from,
        'date_to': date_to,
        'txn_type': txn_type,
    })


@superadmin_required
@require_POST
def wallet_admin_activate(request, parent_id):
    parent_account = get_object_or_404(ParentAccount, id=parent_id)
    try:
        zainpay_service.create_virtual_account(parent_account)
        messages.success(request, 'Virtual account activated successfully.')
    except VirtualAccountCreationError as e:
        messages.error(request, f'Could not activate virtual account: {e}')
    return redirect('wallet_admin_detail', parent_id=parent_id)


@superadmin_required
@require_POST
def wallet_admin_add_child(request, parent_id):
    parent_account = get_object_or_404(ParentAccount, id=parent_id)
    form = AddChildForm(request.POST)
    if form.is_valid():
        admission_number = form.cleaned_data['admission_number'].strip()
        student = Student.objects.filter(admission_number=admission_number).first()
        if not student:
            messages.error(request, f'No student found with admission number "{admission_number}".')
        elif ParentStudentLink.objects.filter(parent_account=parent_account, student=student).exists():
            messages.error(request, f'{student.first_name} {student.last_name} is already linked to this account.')
        else:
            ParentStudentLink.objects.create(parent_account=parent_account, student=student)
            messages.success(request, f'{student.first_name} {student.last_name} added successfully.')
    else:
        messages.error(request, 'Please enter a valid admission number.')
    return redirect('wallet_admin_detail', parent_id=parent_id)


@superadmin_required
@require_POST
def wallet_admin_remove_child(request, parent_id, link_id):
    parent_account = get_object_or_404(ParentAccount, id=parent_id)
    link = get_object_or_404(ParentStudentLink, id=link_id, parent_account=parent_account)
    name = f"{link.student.first_name} {link.student.last_name}"
    link.delete()
    messages.success(request, f'{name} removed from account.')
    return redirect('wallet_admin_detail', parent_id=parent_id)


@superadmin_required
def wallet_admin_make_payment(request, parent_id):
    parent_account = get_object_or_404(ParentAccount.objects.select_related('wallet'), id=parent_id)
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
    other_fee_structures = OtherFeeStructure.objects.filter(active=True) if fee_type == 'other_fees' else OtherFeeStructure.objects.none()

    return render(request, 'src/wallet_admin_make_payment.html', {
        'parent_account': parent_account,
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


@superadmin_required
def wallet_admin_pay_fees(request, parent_id):
    parent_account = get_object_or_404(ParentAccount.objects.select_related('wallet'), id=parent_id)
    wallet = parent_account.wallet

    fee_type = request.GET.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'other_fees'):
        fee_type = 'school_fees'

    session_id = request.GET.get('session_id', '').strip()
    term_id = request.GET.get('term_id', '').strip()

    if not session_id or not term_id:
        messages.error(request, 'Please select both a session and a term.')
        return redirect('wallet_admin_make_payment', parent_id=parent_id)

    session = get_object_or_404(Session, id=session_id)
    term = get_object_or_404(Term, id=term_id, session=session)
    student_ids = request.GET.getlist('student_id')

    student_type = _clean_student_type(request)
    transport = _clean_transport(request)

    if fee_type == 'school_fees':
        fee_selections = _build_school_fee_selections(parent_account, session, term, student_ids, student_type, transport)
    else:
        fee_ids = request.GET.getlist('fee_id')
        fee_selections = _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids)

    total_amount = sum(item['amount'] for item in fee_selections)
    estimated_fee = settings.ZAINPAY_TRANSFER_FEE_ESTIMATE
    can_pay = bool(fee_selections) and wallet.balance >= (total_amount + estimated_fee)
    unique_student_ids = list(dict.fromkeys(item['student'].id for item in fee_selections))

    return render(request, 'src/wallet_admin_pay_fees.html', {
        'parent_account': parent_account,
        'session': session,
        'term': term,
        'fee_type': fee_type,
        'student_type': student_type,
        'transport': transport,
        'fee_selections': fee_selections,
        'unique_student_ids': unique_student_ids,
        'total_amount': total_amount,
        'wallet': wallet,
        'estimated_fee': estimated_fee,
        'can_pay': can_pay,
        'student_ids': student_ids,
        'fee_ids': request.GET.getlist('fee_id') if fee_type == 'other_fees' else [],
    })


@superadmin_required
@require_POST
def wallet_admin_confirm_payment(request, parent_id):
    parent_account = get_object_or_404(ParentAccount, id=parent_id)

    fee_type = request.POST.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'other_fees'):
        fee_type = 'school_fees'

    session = get_object_or_404(Session, id=request.POST.get('session_id'))
    term = get_object_or_404(Term, id=request.POST.get('term_id'), session=session)
    student_ids = request.POST.getlist('student_id')

    if fee_type == 'school_fees':
        student_type = _clean_student_type(request)
        transport = _clean_transport(request)
        fee_selections = _build_school_fee_selections(parent_account, session, term, student_ids, student_type, transport)
    else:
        fee_ids = request.POST.getlist('fee_id')
        fee_selections = _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids)

    if not fee_selections:
        messages.error(request, 'Nothing to pay.')
        return redirect('wallet_admin_detail', parent_id=parent_id)

    try:
        wallet_payment = wallet_service.pay_school_fees_from_wallet(parent_account, fee_selections, session, term)
        messages.success(request, 'Payment completed successfully.')
        return redirect('wallet_admin_detail', parent_id=parent_id)
    except InsufficientFundsError:
        messages.error(request, 'Insufficient wallet balance.')
    except (ZainpayTransferError, WalletInactiveError, ValueError) as e:
        messages.error(request, f'Payment failed: {e}')

    return redirect('wallet_admin_detail', parent_id=parent_id)
