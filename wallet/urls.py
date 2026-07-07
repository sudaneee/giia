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
    path('children/<int:link_id>/remove/', views.remove_child, name='remove_child'),
    path('wallet/', views.wallet_overview, name='wallet_overview'),
    path('wallet/fund/', views.wallet_fund_initiate, name='wallet_fund_initiate'),
    path('wallet/fund/callback/', views.wallet_fund_callback, name='wallet_fund_callback'),
    path('wallet/transactions/', views.wallet_transactions, name='wallet_transactions'),
    path('fees/', views.make_payment, name='make_payment'),
    path('fees/pay/', views.pay_fees, name='pay_fees'),
    path('fees/pay/confirm/', views.confirm_wallet_payment, name='confirm_wallet_payment'),
    path('receipts/', views.receipt_list, name='receipt_list'),
    path('receipts/<str:reference>/', views.receipt_detail, name='receipt_detail'),
    path('webhooks/zainpay/', webhooks.zainpay_deposit_webhook, name='zainpay_webhook'),
    path('webhooks/paystack/', webhooks.paystack_funding_webhook, name='paystack_funding_webhook'),
]
