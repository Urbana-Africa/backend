from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed
from django.utils.translation import gettext_lazy as _
from rest_framework import HTTP_HEADER_ENCODING


class CookieJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication that supports:
    - Standard Authorization: Bearer <token> header (preferred for mobile/API clients)
    - access_token cookie (for web/SPA with HttpOnly cookies)

    Used in Refresh Ghana to allow secure, cookie-based auth for business owners
    managing their listings from the browser without exposing tokens in localStorage.
    """
    def authenticate(self, request):
        """
        Attempt authentication using:
        1. Authorization header (Bearer token) — highest priority
        2. 'access_token' cookie — fallback for browser sessions
        """
        # 1. Try standard JWT header first (Authorization: Bearer ...)
        header = self.get_header(request)
        if header is not None:
            raw_token = self.get_raw_token(header)
            if raw_token is not None:
                return self._authenticate_with_token(raw_token, request)

        # 2. Fallback to cookie if no valid header token
        raw_token = request.COOKIES.get('access_token')
        if raw_token is None:
            return None

        return self._authenticate_with_token(raw_token, request)

    def _authenticate_with_token(self, raw_token, request):
        """
        Shared validation logic to avoid duplication.
        """
        try:
            validated_token = self.get_validated_token(raw_token)
            user = self.get_user(validated_token)
            return user, validated_token
        except InvalidToken as e:
            # Let DRF handle 401 properly instead of raising here
            raise AuthenticationFailed(
                _("Invalid or expired access token"),
                code="invalid_token"
            ) from e
        except Exception as e:
            raise AuthenticationFailed(
                _("Authentication failed: %(error)s") % {"error": str(e)},
                code="authentication_failed"
            ) from e

    def get_header(self, request):
        """
        Custom header extraction with better encoding handling.
        """
        header = request.META.get("HTTP_AUTHORIZATION", b"")
        if isinstance(header, str):
            # Work around django test client oddness
            header = header.encode(HTTP_HEADER_ENCODING)

        return header