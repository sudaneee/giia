from django.urls import path

from . import views, webhooks

app_name = 'wallet'

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.parent_login, name='login'),
    path('logout/', views.parent_logout, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('children/', views.children_list, name='children_list'),
    path('children/add/', views.add_child, name='add_child'),
    path('wallet/', views.wallet_overview, name='wallet_overview'),
    path('wallet/activate/', views.wallet_activate, name='wallet_activate'),
    path('wallet/transactions/', views.wallet_transactions, name='wallet_transactions'),
    path('fees/', views.outstanding_fees, name='outstanding_fees'),
    path('fees/pay/', views.pay_fees, name='pay_fees'),
    path('fees/pay/confirm/', views.confirm_wallet_payment, name='confirm_wallet_payment'),
    path('receipts/', views.receipt_list, name='receipt_list'),
    path('receipts/<str:reference>/', views.receipt_detail, name='receipt_detail'),
    path('webhooks/zainpay/', webhooks.zainpay_deposit_webhook, name='zainpay_webhook'),
]
