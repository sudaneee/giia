from django.urls import path
from website import views

urlpatterns = [
    path('', views.home, name='home'),
    # path('login', views.loginPage, name='login'),
    # path('logout', views.logoutUser, name='logout'),
    path('about', views.about, name='about'),

    path('gallery', views.gallery, name='gallery'),
    path('gallery/<pk>', views.single_gallery, name='single-gallery'),
    path('staff-single/<pk>', views.staff_single, name='staff-single'),
    path('news', views.news, name="news"),
    path('news/<pk>', views.news_single, name='news-single'),
    path('contact', views.contact, name="website-contact"),
    path('journals', views.journals, name="journals"),
]
