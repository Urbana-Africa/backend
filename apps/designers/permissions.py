from rest_framework.permissions import BasePermission


class IsDesigner(BasePermission):
    """
    Allows access only to users with the ``designer`` role, plus admin
    staff (support/ops) who may need to act on a designer's behalf.

    The signup flow assigns ``user_type='designer'`` before the user
    reaches the profile-setup screen, so this permission does not block
    onboarding.
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return user.user_type in ("designer", "admin")
