import logging
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from src.models import OtherFeeStructure, Session, Student, Term

from .api_permissions import IsParentAccount
from .api_serializers import (
    FeeSelectionSerializer,
    OtherFeeStructureSerializer,
    ParentStudentLinkSerializer,
    SessionSerializer,
    WalletPaymentDetailSerializer,
    WalletPaymentSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .forms import AddChildForm, ParentRegistrationForm, ParentSetPasswordForm
from .models import ParentAccount, ParentStudentLink, WalletFundingRequest, WalletPayment
from .services import fee_service, paystack_service, wallet_service, zainpay_service
from .services.exceptions import (
    InsufficientFundsError,
    WalletInactiveError,
    ZainpayCheckoutError,
    ZainpayTransferError,
)
# Reused as-is rather than re-implemented: these already encode the "never
# trust client-submitted amounts, recompute the fee breakdown server-side"
# rule the HTML pay-fees flow relies on. Same for the password-reset helpers
# below - one token/eligibility policy shared by the HTML views and here.
from .views import (
    PASSWORD_RESET_SENT_MESSAGE,
    VALID_STUDENT_TYPES,
    _build_other_fee_selections,
    _build_password_reset_url,
    _build_school_fee_selections,
    _find_resettable_parent_user,
    _resolve_password_reset_user,
    _send_password_reset_email,
)

logger = logging.getLogger(__name__)


def _clean_student_type(value):
    return value if value in VALID_STUDENT_TYPES else 'returning'


def _clean_transport(value):
    return value in ('true', True)


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    """
    JWT equivalent of wallet.views.parent_login: same identifier (email or
    username) + password check, same "parents only" rule, but returns a
    token pair instead of starting a session.
    """
    identifier = (request.data.get('identifier') or '').strip()
    password = request.data.get('password') or ''

    if not identifier or not password:
        return Response(
            {'detail': 'Both identifier and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    username = identifier
    if '@' in identifier:
        user_obj = User.objects.filter(email__iexact=identifier).first()
        if user_obj:
            username = user_obj.username

    user = authenticate(request, username=username, password=password)

    if user is None:
        return Response({'detail': 'Invalid email/username or password.'}, status=status.HTTP_401_UNAUTHORIZED)

    if not hasattr(user, 'parent_account'):
        return Response({'detail': 'This login is for parents only.'}, status=status.HTTP_403_FORBIDDEN)

    refresh = RefreshToken.for_user(user)

    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'parent': {
            'id': user.parent_account.id,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'phone_number': user.parent_account.phone_number,
        },
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    """
    JSON equivalent of wallet.views.register - reuses ParentRegistrationForm
    for the exact same validation (email uniqueness, password match/strength)
    the HTML signup form applies, then logs straight in with a token pair
    instead of a session, same as login() above.
    """
    form = ParentRegistrationForm(request.data)
    if not form.is_valid():
        return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

    data = form.cleaned_data
    with transaction.atomic():
        user = User.objects.create_user(
            username=data['email'],
            email=data['email'],
            password=data['password1'],
            first_name=data['first_name'],
            last_name=data['last_name'],
        )
        ParentAccount.objects.create(user=user, phone_number=data['phone_number'])

    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'parent': {
            'id': user.parent_account.id,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'phone_number': user.parent_account.phone_number,
        },
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    """
    JSON equivalent of wallet.views.password_reset_request. Always returns
    200 with the same generic message whether or not the email matches a
    resettable account, so this can't be used to enumerate registered
    parent emails. The email itself links to the HTML password_reset_confirm
    page (it has to be something a mail client can open), not back into the
    app - password_reset_confirm below exists for a future in-app deep link,
    not for this email flow.
    """
    email = (request.data.get('email') or '').strip()
    if not email:
        return Response({'detail': 'email is required.'}, status=status.HTTP_400_BAD_REQUEST)

    user = _find_resettable_parent_user(email)
    if user:
        _send_password_reset_email(user, _build_password_reset_url(request, user))

    return Response({'detail': PASSWORD_RESET_SENT_MESSAGE})


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    """
    JSON equivalent of wallet.views.password_reset_confirm - takes the same
    uidb64/token pair from the emailed link plus a new password, reusing
    ParentSetPasswordForm for identical validation.
    """
    uidb64 = request.data.get('uidb64') or ''
    token = request.data.get('token') or ''

    user = _resolve_password_reset_user(uidb64, token)
    if not user:
        return Response({'detail': 'This password reset link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

    form = ParentSetPasswordForm(request.data)
    if not form.is_valid():
        return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(form.cleaned_data['new_password1'])
    user.save()
    return Response({'detail': 'Your password has been reset. You can now log in.'})


@api_view(['GET'])
@permission_classes([IsParentAccount])
def dashboard(request):
    """
    JSON equivalent of wallet.views.dashboard - same three pieces of data
    (wallet, linked children, 5 most recent transactions), reused as-is from
    the same relations the HTML view reads.
    """
    parent_account = request.user.parent_account
    wallet = parent_account.wallet
    links = parent_account.student_links.select_related('student', 'student__enrolled_class').all()
    recent_transactions = wallet.transactions.all()[:5]

    return Response({
        'wallet': WalletSerializer(wallet).data,
        'children': ParentStudentLinkSerializer(links, many=True).data,
        'recent_transactions': WalletTransactionSerializer(recent_transactions, many=True).data,
    })


@api_view(['GET'])
@permission_classes([IsParentAccount])
def me(request):
    """
    Identity check for a stored token with no cached login/register response
    around it (e.g. app relaunch) - deliberately separate from dashboard()
    so restoring "who's logged in" on a splash screen doesn't have to pay
    for wallet/children/transactions queries it doesn't need yet.
    """
    user = request.user
    return Response({
        'id': user.parent_account.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone_number': user.parent_account.phone_number,
    })


@api_view(['POST'])
@permission_classes([IsParentAccount])
def logout(request):
    """
    Revokes the given refresh token via the blacklist app, so a lost/stolen
    device can be logged out server-side instead of just relying on the
    access token's short 30-minute expiry. The access token used to call
    this endpoint is unaffected and stays valid until it naturally expires -
    only the refresh token stops being usable to mint new ones.
    """
    refresh = request.data.get('refresh')
    if not refresh:
        return Response({'detail': 'refresh token is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        RefreshToken(refresh).blacklist()
    except TokenError:
        return Response({'detail': 'Invalid or already-invalidated refresh token.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response(status=status.HTTP_205_RESET_CONTENT)


@api_view(['GET', 'POST'])
@permission_classes([IsParentAccount])
def children(request):
    """
    JSON equivalent of wallet.views.children_list (GET) and add_child (POST).
    """
    parent_account = request.user.parent_account

    if request.method == 'GET':
        links = ParentStudentLink.objects.filter(
            parent_account=parent_account
        ).select_related('student', 'student__enrolled_class')
        return Response(ParentStudentLinkSerializer(links, many=True).data)

    form = AddChildForm(request.data)
    if not form.is_valid():
        return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

    admission_number = form.cleaned_data['admission_number'].strip()
    student = Student.objects.filter(admission_number=admission_number).first()

    if not student:
        return Response(
            {'errors': {'admission_number': [f'No student found with admission number "{admission_number}".']}},
            status=status.HTTP_404_NOT_FOUND,
        )
    if ParentStudentLink.objects.filter(parent_account=parent_account, student=student).exists():
        return Response(
            {'errors': {'admission_number': [f'{student.first_name} {student.last_name} is already linked to your account.']}},
            status=status.HTTP_409_CONFLICT,
        )

    link = ParentStudentLink.objects.create(parent_account=parent_account, student=student)
    link = ParentStudentLink.objects.select_related('student', 'student__enrolled_class').get(id=link.id)
    return Response(ParentStudentLinkSerializer(link).data, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsParentAccount])
def child_detail(request, link_id):
    """JSON equivalent of wallet.views.remove_child."""
    parent_account = request.user.parent_account
    link = get_object_or_404(ParentStudentLink, id=link_id, parent_account=parent_account)
    link.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsParentAccount])
def wallet_overview(request):
    """JSON equivalent of wallet.views.wallet_overview."""
    wallet = request.user.parent_account.wallet
    return Response({
        'wallet': WalletSerializer(wallet).data,
        'funding_provider': settings.WALLET_FUNDING_PROVIDER,
    })


@api_view(['POST'])
@permission_classes([IsParentAccount])
def wallet_fund(request):
    """
    JSON equivalent of wallet.views.wallet_fund_initiate: same
    Zainpay/Paystack branching on settings.WALLET_FUNDING_PROVIDER, but
    returns the checkout URL as JSON instead of redirecting the browser to
    it - the Flutter app opens it in a webview and the existing
    wallet_fund_callback/webhook flow takes it from there unchanged.
    """
    parent_account = request.user.parent_account
    wallet = parent_account.wallet

    if not wallet.is_active:
        return Response({'detail': 'This wallet is not active.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        amount = Decimal(str(request.data.get('amount', '0')))
    except Exception:
        amount = Decimal('0')

    if amount <= 0:
        return Response({'detail': 'Enter a valid amount to fund.'}, status=status.HTTP_400_BAD_REQUEST)

    callback_url = request.build_absolute_uri(reverse('wallet:wallet_fund_callback'))

    if settings.WALLET_FUNDING_PROVIDER == 'zainpay':
        reference = f"WALLETFUND-{uuid4().hex[:12].upper()}"
        WalletFundingRequest.objects.create(wallet=wallet, reference=reference, amount=amount)

        try:
            checkout_url = zainpay_service.initialize_checkout(
                email=request.user.email,
                mobile_number=parent_account.phone_number,
                amount=amount,
                txn_ref=reference,
                callback_url=callback_url,
                zainbox_code=settings.ZAINPAY_WALLET_ZAINBOX_CODE,
            )
        except ZainpayCheckoutError as e:
            return Response({'detail': f'Could not start wallet funding: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({'checkout_url': checkout_url, 'reference': reference})

    payment_method = request.data.get('payment_method', 'card')
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
        callback_url=callback_url,
        metadata={
            'purpose': 'wallet_funding',
            'wallet_id': wallet.id,
            'requested_amount': str(amount),
        },
    )

    if result.get('status') and result.get('data', {}).get('authorization_url'):
        return Response({'checkout_url': result['data']['authorization_url'], 'reference': reference})

    return Response({'detail': 'Could not start wallet funding. Please try again.'}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(['GET'])
@permission_classes([IsParentAccount])
def wallet_transactions(request):
    """JSON equivalent of wallet.views.wallet_transactions, same filters."""
    wallet = request.user.parent_account.wallet
    qs = wallet.transactions.all()

    type_filter = request.query_params.get('type', '')
    if type_filter in ('credit', 'debit'):
        qs = qs.filter(transaction_type=type_filter)

    date_from = request.query_params.get('from', '')
    date_to = request.query_params.get('to', '')
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.query_params.get('page'))

    return Response({
        'results': WalletTransactionSerializer(page_obj.object_list, many=True).data,
        'page': page_obj.number,
        'num_pages': paginator.num_pages,
        'count': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
    })


@api_view(['GET'])
@permission_classes([IsParentAccount])
def fee_options(request):
    """
    JSON equivalent of the sessions/terms/other-fee-structure part of
    wallet.views.make_payment (the "which children" part is covered by the
    children endpoint above - the app already has that list).
    """
    fee_type = request.query_params.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'tahfeez_fees', 'other_fees'):
        fee_type = 'school_fees'

    sessions = Session.objects.all().order_by('-id')

    session_id = request.query_params.get('session_id')
    if not session_id:
        current = sessions.filter(current=True).first()
        session_id = current.id if current else None

    selected_session = Session.objects.filter(id=session_id).first() if session_id else None
    term_id = request.query_params.get('term_id')
    selected_term = Term.objects.filter(id=term_id, session=selected_session).first() if term_id and selected_session else None

    if selected_session and selected_term:
        term_other_fees = OtherFeeStructure.objects.filter(active=True, session=selected_session, term=selected_term)
        tahfeez_fees, transportation_fees, non_special_fees = fee_service.split_special_other_fees(term_other_fees)
    else:
        tahfeez_fees, transportation_fees, non_special_fees = [], [], []

    if fee_type == 'tahfeez_fees':
        other_fee_structures = tahfeez_fees
    elif fee_type == 'other_fees':
        other_fee_structures = non_special_fees
    else:
        other_fee_structures = []

    return Response({
        'sessions': SessionSerializer(sessions, many=True).data,
        'selected_session_id': selected_session.id if selected_session else None,
        'selected_term_id': selected_term.id if selected_term else None,
        'fee_type': fee_type,
        'other_fee_structures': OtherFeeStructureSerializer(other_fee_structures, many=True).data,
        'transportation_fee_structures': OtherFeeStructureSerializer(transportation_fees, many=True).data,
    })


def _fee_selections_from_params(parent_account, params):
    """Shared by fees_preview and fees_pay_confirm - rebuilds fee_selections
    server-side from request params exactly like wallet.views.pay_fees /
    confirm_wallet_payment do, so the amount charged is never trusted from
    the client."""
    fee_type = params.get('fee_type', 'school_fees')
    if fee_type not in ('school_fees', 'tahfeez_fees', 'other_fees'):
        fee_type = 'school_fees'

    session = get_object_or_404(Session, id=params.get('session_id'))
    term = get_object_or_404(Term, id=params.get('term_id'), session=session)
    student_ids = params.getlist('student_id') if hasattr(params, 'getlist') else (params.get('student_ids') or [])

    student_type = _clean_student_type(params.get('student_type', 'returning'))
    transport = _clean_transport(params.get('transport', 'false'))
    promo_code = (params.get('promo_code') or '').strip()

    if fee_type == 'school_fees':
        fee_selections = _build_school_fee_selections(parent_account, session, term, student_ids, student_type, transport)
    elif fee_type == 'tahfeez_fees':
        fee_ids = fee_service.resolve_tahfeez_fee_ids(session, term, transport)
        fee_selections = _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids, promo_code)
    else:
        fee_ids = params.getlist('fee_id') if hasattr(params, 'getlist') else (params.get('fee_ids') or [])
        fee_selections = _build_other_fee_selections(parent_account, session, term, student_ids, fee_ids, promo_code)

    return fee_type, session, term, fee_selections


@api_view(['GET'])
@permission_classes([IsParentAccount])
def fees_preview(request):
    """
    JSON equivalent of wallet.views.pay_fees: given fee_type/session/term/
    student_id(s)/student_type/transport/promo_code query params, recomputes
    the fee breakdown and whether the wallet can afford it right now.
    """
    parent_account = request.user.parent_account
    wallet = parent_account.wallet

    fee_type, session, term, fee_selections = _fee_selections_from_params(parent_account, request.query_params)
    total_amount = sum((item['amount'] for item in fee_selections), Decimal('0.00'))

    required_balance = total_amount
    if settings.WALLET_FUNDING_PROVIDER == 'zainpay' and settings.ZAINPAY_LIVE_TRANSFER_ENABLED:
        required_balance += settings.ZAINPAY_TRANSFER_FEE_ESTIMATE
    can_pay_from_wallet = bool(fee_selections) and wallet.balance >= required_balance

    return Response({
        'fee_type': fee_type,
        'session_id': session.id,
        'term_id': term.id,
        'fee_selections': [FeeSelectionSerializer.from_selection(item) for item in fee_selections],
        'total_amount': total_amount,
        'wallet_balance': wallet.balance,
        'can_pay_from_wallet': can_pay_from_wallet,
    })


@api_view(['POST'])
@permission_classes([IsParentAccount])
def fees_pay_confirm(request):
    """JSON equivalent of wallet.views.confirm_wallet_payment."""
    parent_account = request.user.parent_account

    fee_type, session, term, fee_selections = _fee_selections_from_params(parent_account, request.data)

    if not fee_selections:
        return Response({'detail': 'Nothing to pay.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        wallet_payment = wallet_service.pay_school_fees_from_wallet(parent_account, fee_selections, session, term)
    except InsufficientFundsError:
        return Response({'detail': 'Insufficient wallet balance for this payment.'}, status=status.HTTP_402_PAYMENT_REQUIRED)
    except (ZainpayTransferError, WalletInactiveError, ValueError) as e:
        return Response({'detail': f'Payment could not be completed: {e}'}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        WalletPaymentDetailSerializer(wallet_payment).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET'])
@permission_classes([IsParentAccount])
def receipts(request):
    """JSON equivalent of wallet.views.receipt_list."""
    parent_account = request.user.parent_account
    wallet_payments = WalletPayment.objects.filter(
        wallet_transaction__wallet=parent_account.wallet
    ).select_related('wallet_transaction', 'session', 'term').order_by('-created_at')

    return Response(WalletPaymentSerializer(wallet_payments, many=True).data)


@api_view(['GET'])
@permission_classes([IsParentAccount])
def receipt_detail(request, reference):
    """JSON equivalent of wallet.views.receipt_detail."""
    parent_account = request.user.parent_account
    wallet_payment = get_object_or_404(
        WalletPayment,
        wallet_transaction__reference=reference,
        wallet_transaction__wallet=parent_account.wallet,
    )
    return Response(WalletPaymentDetailSerializer(wallet_payment).data)
