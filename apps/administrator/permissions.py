from rest_framework.permissions import BasePermission

class IsSuperAdmin(BasePermission):
    """
    Allows access only to superadmin users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role == 'superadmin'
        )

class IsSupportAgent(BasePermission):
    """
    Allows access to support_agent and superadmin users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role in ['support_agent', 'superadmin']
        )

class IsProductManager(BasePermission):
    """
    Allows access to product_manager and superadmin users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role in ['product_manager', 'superadmin']
        )
