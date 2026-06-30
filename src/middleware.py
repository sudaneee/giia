from django.shortcuts import redirect
from django.urls import reverse


class ParentPortalOnlyMiddleware:
    """
    Redirects parent account users away from the school staff system.
    Parent accounts are Django users with a ParentAccount relation — they
    have their own portal at /parent/ and must not access /school/ views,
    even if they happen to be authenticated in the same browser session.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.startswith('/school/')
            and request.user.is_authenticated
            and hasattr(request.user, 'parent_account')
        ):
            return redirect(reverse('wallet:dashboard'))
        return self.get_response(request)
