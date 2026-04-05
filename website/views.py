
from .models import Course, Gallery, Blog
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .models import Staff




def is_staff(user):
    if user.staff:
        return True

    


# def loginPage(request):
    
#     page = 'login'
#     if request.user.is_authenticated:
#         if hasattr(request.user, 'staff'):
#             return redirect(reverse('staff-dashboard'))
#         elif hasattr(request.user, 'student'):
#             return redirect(reverse('student-dashboard'))
#         # return redirect('home')
#         else:
#             return redirect(reverse('admin:index'))


#     if request.method == 'POST':
#         username = request.POST.get('username')
#         password = request.POST.get('password')


#         try:
#             user = User.objects.get(username=username)
            
#         except:
#             messages.error(request, 'User does not exist')

#         user = authenticate(request, username=username, password=password)


#         if user is not None:
#             login(request, user)
#             if hasattr(user, 'student'):
#                 return redirect(reverse('student-dashboard'))
#             elif hasattr(user, 'staff'):
#                 return redirect(reverse('staff-dashboard'))
                  
#         messages.error(request, 'Username OR password does not exit')
#         return redirect(reverse('login'))

#     context = {'page': page}
#     return render(request, 'website/login.html')



# def logoutUser(request):
#     logout(request)
#     return redirect('login')




def home(request):
   
    return render(request, 'website/index.html')


def about(request):
    return render(request, 'website/about.html')



def gallery(request):

    gallery_object = Gallery.objects.all()
    paginator = Paginator(gallery_object, 9)

    page = request.GET.get('page')
    try:
        images = paginator.page(page)
    except PageNotAnInteger:
        images = paginator.page(1)
    except EmptyPage:
        images = paginator.page(paginator.num_pages)
    context = {
        'images': images,
    }

    return render(request, 'website/gallery.html', context)



def single_gallery(request, pk):
    c = Gallery.objects.get(id=pk)
    context = {
        'single_gallery': c,
    }
    return render(request, 'website/single-gallery.html', context)



def news(request):
    news_list = Blog.objects.all()
    paginator = Paginator(news_list, 3)  # Show 10 news items per page

    page = request.GET.get('page')
    try:
        news = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        news = paginator.page(1)
    except EmptyPage:
        # If page is out of range (e.g. 9999), deliver last page of results.
        news = paginator.page(paginator.num_pages)

    context = {
        'news': news
    }
    return render(request, 'website/news.html', context)


def news_single(request, pk):
    c = Blog.objects.get(id=pk)
    b = Blog.objects.all()
    context = {
        'news_single': c,
        'news': b
    }
    return render(request, 'website/news-single.html', context)

def staff_single(request, pk):
    c = Staff.objects.get(id=pk)
    context = {
        'staff_single': c,
    }
    return render(request, 'website/staff-single.html', context)


def contact(request):
    return render(request, 'website/contact.html')


def journals(request):
    return render(request, 'website/journals.html')