from functools import wraps
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse


def parent_required(view_func):
    """
    Restricts a view to authenticated users that have a linked ParentAccount.
    Staff/admin accounts (no ParentAccount) are redirected to the parent login,
    keeping parent and staff authentication completely separate.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not hasattr(request.user, 'parent_account'):
            login_url = reverse('wallet:login')
            return redirect(f"{login_url}?{urlencode({'next': request.path})}")
        return view_func(request, *args, **kwargs)
    return wrapper
