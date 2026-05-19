from rest_framework.permissions import BasePermission


# Allow access only to staff users.
class IsAdminUser(BasePermission):
    # Check whether the current user is staff.
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)