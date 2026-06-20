from rest_framework.permissions import BasePermission

class IsSuperAdmin(BasePermission):
    """
    Allows access only to superadmin and c_level users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role in ['superadmin', 'c_level']
        )

class IsSupportAgent(BasePermission):
    """
    Allows access to support_agent, superadmin, and c_level users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role in ['support_agent', 'superadmin', 'c_level']
        )

class IsProductManager(BasePermission):
    """
    Allows access to product_manager, superadmin, and c_level users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role in ['product_manager', 'superadmin', 'c_level']
        )

class IsMarketer(BasePermission):
    """
    Allows access to marketer, superadmin, and c_level users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role in ['marketer', 'superadmin', 'c_level']
        )

class IsCLevel(BasePermission):
    """
    Allows access EXCLUSIVELY to c_level users. Superadmin is blocked.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == 'admin' and
            request.user.admin_role == 'c_level'
        )
