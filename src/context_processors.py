
from website.models import GeneralInformation, Carousel, Picture, Paragraph, Course, Staff, Blog, Journal
from django.contrib.auth.models import User
# from portal.models import *


def data_processor(request):
    query = GeneralInformation.objects.all()[0]
    carousels = Carousel.objects.all()
    about1 = Picture.objects.get(title="about1")
    choose1 = Picture.objects.get(title="choose1")
    car5 = Picture.objects.get(title="car5")
    result_checker = Picture.objects.get(title="result-checker")
    grading_system = Picture.objects.get(title="grading-system")
    choose_p = Paragraph.objects.get(title="choose_p")
    # courses = Course.objects.all()
    staffs = Staff.objects.all()
    blogs = Blog.objects.all()
    about_header_bg = Picture.objects.get(title="about header bg")
    # programmes = Programme.objects.all()

    path = request.path
    # Logic to extract the page title from the path
    # For example, if your URL is '/blog/article/', you could extract 'article' as the title
    page_title = path.strip('/').split('/')[-1].replace('-', ' ').capitalize()
    journals = Journal.objects.all()
    


    return {
        'data' : query,
        'carousels': carousels, 
        'about1': about1,
        'choose1': choose1,
        'car5': car5,
        'result_checker': result_checker,
        'grading_system': grading_system,
        'choose_p': choose_p,
        # 'courses': courses,
        'staffs': staffs,
        'blogs': blogs,
        'about_header_bg': about_header_bg,
        'current_path': request.path,
        # 'programmes': programmes,
  
        'page_title': page_title,
        'journals': journals,

    }