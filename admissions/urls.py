from django.urls import path

from . import views

app_name = 'admissions'

urlpatterns = [
    path('', views.apply, name='apply'),
    path('callback/', views.apply_callback, name='apply_callback'),
]
