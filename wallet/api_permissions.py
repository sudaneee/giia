from rest_framework.permissions import BasePermission


class IsParentAccount(BasePermission):
    """
    API equivalent of the @parent_required decorator used by the HTML views:
    the JWT must belong to an authenticated user that has a linked
    ParentAccount. Staff/admin accounts have no ParentAccount and are
    rejected, keeping parent and staff auth completely separate here too.
    """

    message = 'This login is for parents only.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, 'parent_account')
        )
