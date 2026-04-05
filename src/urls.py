# src/urls.py

from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('students/', views.student_list, name='student_list'),
    path('students/add/', views.add_student, name='add_student'),
    path('students/update/<int:student_id>/', views.update_student, name='update_student'),
    path('students/delete/<int:student_id>/', views.delete_student, name='delete_student'),
    path('students/bulk-upload/', views.bulk_upload_students, name='bulk_upload_students'),
    path('students/download-template/', views.download_excel_template, name='download_excel_template'),
    path('students/not-admitted/', views.not_admitted_students, name='not_admitted_students'),
    
    path("students/promote-students/", views.promote_students, name="promote_students"),
    
    path('students/download-not-admitted-template/', views.download_not_admitted_template, name='download_not_admitted_template'),
    path('students/admit-via-excel/', views.not_admitted_students, name='admit_students_via_excel'),  # Bulk admission via Excel now integrated in not_admitted_students view
    path('students/generate-admission-letter/<int:student_id>/', views.generate_admission_letter, name='generate_admission_letter'),  # URL for generating admission letters
    path('students/admitted/', views.admitted_students, name='admitted_students'),  # New URL for public view

    # Subject URLs
    path('subjects/', views.subject_list, name='subject_list'),
    path('subjects/create/', views.subject_create, name='subject_create'),
    path('subjects/update/<int:pk>/', views.subject_update, name='subject_update'),
    path('subjects/delete/<int:pk>/', views.subject_delete, name='subject_delete'),

    # Session URLs
    path('sessions/', views.session_list, name='session_list'),
    path('sessions/create/', views.session_create, name='session_create'),
    path('sessions/update/<int:pk>/', views.session_update, name='session_update'),
    path('sessions/delete/<int:pk>/', views.session_delete, name='session_delete'),

    # Term URLs
    path('terms/', views.term_list, name='term_list'),
    path('terms/create/', views.term_create, name='term_create'),
    path('terms/update/<int:pk>/', views.term_update, name='term_update'),
    path('terms/delete/<int:pk>/', views.term_delete, name='term_delete'),

    # SchoolClass URLs
    path('schoolclasses/', views.schoolclass_list, name='schoolclass_list'),
    path('schoolclasses/create/', views.schoolclass_create, name='schoolclass_create'),
    path('schoolclasses/update/<int:pk>/', views.schoolclass_update, name='schoolclass_update'),
    path('schoolclasses/delete/<int:pk>/', views.schoolclass_delete, name='schoolclass_delete'),

    # FeeStructure URLs
    path('feestructures/', views.feestructure_list, name='feestructure_list'),
    path('feestructures/create/', views.feestructure_create, name='feestructure_create'),
    path('feestructures/update/<int:pk>/', views.feestructure_update, name='feestructure_update'),
    path('feestructures/delete/<int:pk>/', views.feestructure_delete, name='feestructure_delete'),

    # Payment URLs
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/create/', views.payment_create, name='payment_create'),
    path('payments/update/<int:pk>/', views.payment_update, name='payment_update'),
    path('payments/delete/<int:pk>/', views.payment_delete, name='payment_delete'),
    path('payments/export/', views.payment_export_excel, name='payment_export_excel'),

    # Category URLs
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/update/<int:pk>/', views.category_update, name='category_update'),
    path('categories/delete/<int:pk>/', views.category_delete, name='category_delete'),

    # Supplier URLs
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/create/', views.supplier_create, name='supplier_create'),
    path('suppliers/update/<int:pk>/', views.supplier_update, name='supplier_update'),
    path('suppliers/delete/<int:pk>/', views.supplier_delete, name='supplier_delete'),

    # Item URLs
    path('items/', views.item_list, name='item_list'),
    path('items/create/', views.item_create, name='item_create'),
    path('items/update/<int:pk>/', views.item_update, name='item_update'),
    path('items/delete/<int:pk>/', views.item_delete, name='item_delete'),

    # Inventory Transaction URLs
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('transactions/create/', views.transaction_create, name='transaction_create'),
    path('transactions/update/<int:pk>/', views.transaction_update, name='transaction_update'),
    path('transactions/delete/<int:pk>/', views.transaction_delete, name='transaction_delete'),

    # Purchase Order URLs
    path('purchase-orders/', views.purchase_order_list, name='purchase_order_list'),
    path('purchase-orders/create/', views.purchase_order_create, name='purchase_order_create'),
    path('purchase-orders/update/<int:pk>/', views.purchase_order_update, name='purchase_order_update'),
    path('purchase-orders/delete/<int:pk>/', views.purchase_order_delete, name='purchase_order_delete'),
    
    # Result
    path('results/entry/', views.result_entry, name='result_entry'),
    path('results/update/', views.result_update, name='result_update'),
    path('results/download-template/', views.download_template, name='download_template'),
    path('results/upload/', views.upload_results, name='upload_results'),
    path('result/download-template/', views.download_result_template, name='download_result_template'),
    path('results/upload-missed/', views.upload_missed_results, name='upload_missed_results'),
    path('results/view_class/', views.select_class_for_result, name='select_class_for_result'),
    path('results/view_class/results/<int:session_id>/<int:term_id>/<int:class_id>/', views.display_class_results, name='display_class_results'),
    
    # Tahfeez Result
    path('results/entry/tahfeez', views.result_entry_tahfeez, name='result_entry_tahfeez'),
    path('results/update/tahfeez', views.result_update_tahfeez, name='result_update_tahfeez'),
    path('results/view_class/tahfeez', views.select_class_for_result_tahfeez, name='select_class_for_result_tahfeez'),
    path('results/view_class/results/tahfeez/<int:session_id>/<int:term_id>/<int:class_id>/', views.display_class_results_tahfeez, name='display_class_results_tahfeez'),
    
    # Delete Result Tahfeez
    path('delete-results-tahfeez/', views.delete_result_tahfeez, name='delete_result_tahfeez'),

    # Result Summary 
    path('results/view_class/summary', views.select_class_for_result_summary, name='select_class_for_result_summary'),
    path('results/view_class/results/summary/<int:session_id>/<int:term_id>/<int:class_id>/', views.display_class_results_summary, name='display_class_results_summary'),
    path('export-results/<int:school_class_id>/<int:term_id>/<int:session_id>/', views.export_results_to_excel, name='export_results'),


    # Delete Result
    path('delete-results/', views.delete_result, name='delete_results'),

    path('student-result-search/', views.student_result_search, name='student_result_search'),
    path('view-student-result/', views.view_student_result, name='view_student_result'),

    # Result Checker
    path('result-checker/', views.result_checker, name='result_checker'),
    path('results/view_student/result/<int:session_id>/<int:term_id>/<int:student_id>/<str:token_code>/', views.display_single_result, name='display_single_result'),



    # Behaivioural Assesment
    path('behavioral/download-template/', views.download_behavioral_template, name='download_behavioral_template'),
    path('behavioral/upload/', views.upload_behavioral_assessments, name='upload_behavioral_assessments'),
    path('behavioral/view/', views.view_behavioral_assessments, name='view_behavioral_assessments'),


    # Mid term
    path('upload-midterm-results/', views.upload_midterm_results, name='upload_midterm_results'),
    path('bulk-update-midterm-results/', views.bulk_midterm_result_update, name='bulk_update_midterm_results'),

    path('mid-term-results/view_class/', views.select_class_for_midterm_result, name='select_class_for_midterm_result'),
    path('mid-term-results/view_class/results/<int:session_id>/<int:term_id>/<int:class_id>/', views.display_midterm_results, name='display_midterm_results'),



    path('pay/', views.payment_entry, name='payment_entry'),
    path('pay/other-fees/', views.other_fee_payment, name='other_fee_payment'),
    path("pay/school-fees", views.public_payment, name="public_payment"),
    path("pay/initialize/", views.initialize_paystack, name="initialize_paystack"),
    path("pay/callback/", views.paystack_callback, name="paystack_callback"),
    path("parent/dashboard/", views.parent_dashboard, name="parent_dashboard"),
    path("pay/receipt/<str:reference>/", views.payment_receipt, name="payment_receipt"),



    # Fee Components
    path("fee-components/", views.feecomponent_list, name="feecomponent_list"),
    path("fee-components/create/", views.feecomponent_create, name="feecomponent_create"),
    path("fee-components/<int:pk>/update/", views.feecomponent_update, name="feecomponent_update"),
    path("fee-components/<int:pk>/delete/", views.feecomponent_delete, name="feecomponent_delete"),


    # Part-Payment Approval
    path("part-payment-approvals/", views.approval_list, name="approval_list"),
    path("part-payment-approvals/create/", views.approval_create, name="approval_create"),
    path("part-payment-approvals/<int:pk>/update/", views.approval_update, name="approval_update"),
    path("part-payment-approvals/<int:pk>/delete/", views.approval_delete, name="approval_delete"),



    # Fee Waivers
    path("fee-waivers/", views.waiver_list, name="waiver_list"),
    path("fee-waivers/create/", views.waiver_create, name="waiver_create"),
    path("fee-waivers/<int:pk>/update/", views.waiver_update, name="waiver_update"),
    path("fee-waivers/<int:pk>/delete/", views.waiver_delete, name="waiver_delete"),

    # path("fee-compliance/", views.class_fee_compliance, name="class_fee_compliance"),
    path("reports/class-fee-compliance/", views.class_fee_compliance, name="class_fee_compliance"),


    path(
    "reports/student-payment-status/",
    views.student_payment_status_report,
    name="student_payment_status_report"
    ),

    # path('results/<int:session_id>/<int:term_id>/<int:class_id>/',
        #  views.display_class_results, name='display_class_results'),

    path('results/download-all/<int:session_id>/<int:term_id>/<int:class_id>/',
         views.download_all_results_pdf, name='download_all_results_pdf'),

    path('results/download/<int:student_id>/<int:session_id>/<int:term_id>/<int:class_id>/',
         views.download_single_result_pdf, name='download_single_result_pdf'),
]





