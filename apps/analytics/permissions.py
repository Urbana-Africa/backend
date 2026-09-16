from rest_framework.permissions import BasePermission


class IsStaffAdmin(BasePermission):
    """Any authenticated staff member (user_type == 'admin'), any admin_role."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == 'admin'
        )
