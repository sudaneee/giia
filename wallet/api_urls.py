from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import api_views

app_name = 'wallet_api'

urlpatterns = [
    path('auth/register/', api_views.register, name='register'),
    path('auth/login/', api_views.login, name='login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='refresh'),
    path('auth/logout/', api_views.logout, name='logout'),
    path('auth/password-reset/', api_views.password_reset_request, name='password_reset_request'),
    path('auth/password-reset/confirm/', api_views.password_reset_confirm, name='password_reset_confirm'),
    path('me/', api_views.me, name='me'),
    path('dashboard/', api_views.dashboard, name='dashboard'),
    path('children/', api_views.children, name='children'),
    path('children/<int:link_id>/', api_views.child_detail, name='child_detail'),
    path('wallet/', api_views.wallet_overview, name='wallet_overview'),
    path('wallet/fund/', api_views.wallet_fund, name='wallet_fund'),
    path('wallet/transactions/', api_views.wallet_transactions, name='wallet_transactions'),
    path('fees/options/', api_views.fee_options, name='fee_options'),
    path('fees/preview/', api_views.fees_preview, name='fees_preview'),
    path('fees/pay/confirm/', api_views.fees_pay_confirm, name='fees_pay_confirm'),
    path('receipts/', api_views.receipts, name='receipts'),
    path('receipts/<str:reference>/', api_views.receipt_detail, name='receipt_detail'),
]
