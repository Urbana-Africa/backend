from pathlib import Path
from datetime import timedelta
from decouple import config

# =====================================================
# Base Paths & Environment
# =====================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = True
# DEBUG = config("DEBUG", default=False, cast=bool)
ENV = config("ENV", default="dev").lower()  # dev | prod | staging

if ENV not in ["dev", "prod", "staging"]:
    raise ValueError(f"Invalid ENV value: {ENV}")

IS_PRODUCTION = ENV == "prod"
IS_DEVELOPMENT = ENV == "dev"

# =====================================================
# Hosts & Allowed
# =====================================================

ALLOWED_HOSTS = ["api.urbanaafrica.com"]

if not IS_PRODUCTION:
    ALLOWED_HOSTS += ["127.0.0.1", "localhost", "api.urbana.local", "*.urbana.local"]

# =====================================================
# Installed Apps
# =====================================================

INSTALLED_APPS = [
    "daphne",

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    # Third-party
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "channels",
    "django_extensions",
    "django_cleanup.apps.CleanupConfig",
    "tinymce",
    "oauth2_provider",
    "social_django",
    "drf_social_oauth2",
    "django_apscheduler",
    "django_filters",

    # Project apps
    "apps.administrator",
    "apps.algorithm",
    "apps.authentication",
    "apps.customers",
    "apps.designers",
    "apps.pay",
    "apps.core",
    "apps.aps",
    "apps.newsletter"
]

# =====================================================
# Middleware (CORS must be first!)
# =====================================================

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",           # Must be near the top
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# =====================================================
# Core Django Settings
# =====================================================

ROOT_URLCONF = "urbana.urls"
WSGI_APPLICATION = "urbana.wsgi.application"
ASGI_APPLICATION = "urbana.asgi.application"
AUTH_USER_MODEL = "authentication.User"
LOGIN_URL = "/auth/login"
APPEND_SLASH = False

# =====================================================
# Templates
# =====================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# =====================================================
# Database
# =====================================================

if IS_DEVELOPMENT:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    # Use MySQL in production or staging, current setting is just placeholder
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
# =====================================================
# Redis / Channels
# =====================================================

REDIS_URL = config("REDIS_URL", default="redis://127.0.0.1:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    }
}

# =====================================================
# Cookie & Security Settings
# =====================================================

if IS_PRODUCTION:
    COOKIE_DOMAIN = ".urbanaafrica.com"          # shared across all subdomains
    COOKIE_SECURE = True                          # required for SameSite=None
    SESSION_COOKIE_SAMESITE = "None"              # required for cross-subdomain auth
    CSRF_COOKIE_SAMESITE = "None"
    JWT_COOKIE_SAMESITE = "None"
else:
    COOKIE_DOMAIN = ".urbana.local"               # allow sharing across subdomains locally
    COOKIE_SECURE = False
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    JWT_COOKIE_SAMESITE = "Lax"

# Apply domain & secure flags
SESSION_COOKIE_DOMAIN = COOKIE_DOMAIN
SESSION_COOKIE_SECURE = COOKIE_SECURE
SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_DOMAIN = COOKIE_DOMAIN
CSRF_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_HTTPONLY = False  # frontend needs to read it for AJAX

# =====================================================
# REST Framework & JWT (cookie-based for cross-subdomain)
# =====================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        'apps.authentication.jwtauth.CookieJWTAuthentication',  # cookie-based JWT
        # "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
}

SIMPLE_JWT = {

    # Cookie-based JWT (cross-subdomain support)
    "AUTH_COOKIE": "access_token",
    "AUTH_COOKIE_REFRESH": "refresh_token",
    "AUTH_COOKIE_DOMAIN": COOKIE_DOMAIN,
    "AUTH_COOKIE_SECURE": COOKIE_SECURE,
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_SAMESITE": JWT_COOKIE_SAMESITE,
    "AUTH_COOKIE_PATH": "/",
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),          # short!
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,       # Invalidate old refresh tokens via blacklist app
    "UPDATE_LAST_LOGIN": True,
}

# =====================================================
# CORS Configuration
# =====================================================

CORS_ALLOW_CREDENTIALS = True

if IS_PRODUCTION:
    # Explicit list — fastest & most reliable
    CORS_ALLOWED_ORIGINS = [
        "https://urbanaafrica.com",
        "https://www.urbanaafrica.com",
        "https://api.urbanaafrica.com",
        "https://admin.urbanaafrica.com",
        "https://auth.urbanaafrica.com",
        "https://customer.urbanaafrica.com",
        "https://designer.urbanaafrica.com",
        # Add more subdomains here as they are launched
    ]

    # Fallback regex for future/unknown subdomains
    CORS_ALLOWED_ORIGIN_REGEXES = [
        r"^https://([a-z0-9-]+\.)*urbanaafrica\.com$",
    ]

    # CSRF must match exact origins (no regex support)
    CSRF_TRUSTED_ORIGINS = [
        "https://urbanaafrica.com",
        "https://www.urbanaafrica.com",
        "https://api.urbanaafrica.com",
        "https://admin.urbanaafrica.com",
        "https://auth.urbanaafrica.com",
        "https://customer.urbanaafrica.com",
        "https://designer.urbanaafrica.com",
    ]

    # Allow common headers
    CORS_ALLOW_HEADERS = [
        "accept",
        "authorization",
        "content-type",
        "x-csrftoken",
        "x-requested-with",
        "origin",
    ]

    # Headers frontend can access
    CORS_EXPOSE_HEADERS = ["Set-Cookie", "Authorization"]

else:
    # Development: more permissive
    CORS_ALLOWED_ORIGINS = []
    CORS_ALLOWED_ORIGIN_REGEXES = [
        r"^https?://([a-z0-9-]+\.)*urbana\.local(:\d+)?$",
        r"^https?://localhost(:\d+)?$",
        r"^https?://127\.0\.0\.1(:\d+)?$",
    ]
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost:5172", "http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:5176",
        "http://127.0.0.1:5172", "http://127.0.0.1:5173", "http://127.0.0.1:5174", "http://127.0.0.1:5175", "http://127.0.0.1:5176",
        "http://urbana.local:5172", "http://api.urbana.local:8000", "http://admin.urbana.local:5176",
        "http://auth.urbana.local:5173", "http://customer.urbana.local:5175", "http://customer.urbana.local:5176", "http://designer.urbana.local:5174",
        "http://urbana.local", "http://admin.urbana.local", "http://auth.urbana.local", "http://customer.urbana.local", "http://designer.urbana.local",
        "https://urbana.local", "https://admin.urbana.local", "https://auth.urbana.local", "https://customer.urbana.local", "https://designer.urbana.local",
    ]

# =====================================================
# Production Security Headers
# =====================================================

if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000          # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"

# =====================================================
# Static / Media
# =====================================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# =====================================================
# Email Configuration (cleaned — pick one backend)
# =====================================================

# Option 1: Standard SMTP (your original)
# EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# EMAIL_HOST = config("SMTP_HOST")
# EMAIL_PORT = config("SMTP_PORT", cast=int, default=587)
# EMAIL_HOST_USER = config("SMTP_USER")
# EMAIL_HOST_PASSWORD = config("SMTP_PASSWORD")
# EMAIL_USE_TLS = True

# Option 2: Resend (recommended for reliability — uncomment if using)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.resend.com"
APPEND_SLASH=False
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
RESEND_SMTP_PORT = 587
RESEND_SMTP_USERNAME = 'resend'
RESEND_SMTP_HOST = 'smtp.resend.com'
RESEND_API_KEY=config('RESEND_API_KEY')
SMTP_USER = config('SMTP_USER')
SMTP_HOST = config('SMTP_HOST')
SMTP_PASSWORD=config('SMTP_PASSWORD')
SMTP_PORT=config('SMTP_PORT')
# =====================================================
# Misc / Third-party
# =====================================================

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

GEMINI_SECRET_KEY = config("GEMINI_SECRET_KEY", default="")
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
SHIPPO_API_KEY = config("SHIPPO_API_KEY", default="")