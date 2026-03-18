from pathlib import Path
from datetime import timedelta
from decouple import config

# =====================================================
# Base Paths & Environment
# =====================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
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
    "apps.authentication",
    "apps.customers",
    "apps.designers",
    "apps.pay",
    "apps.core",
    "apps.aps",
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
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": config("DB_NAME"),
            "USER": config("DB_USER"),
            "PASSWORD": config("DB_PASSWORD"),
            "HOST": config("DB_HOST", default="localhost"),
            "PORT": config("DB_PORT", default="3306"),
            "OPTIONS": {
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
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
    COOKIE_DOMAIN = None                          # or ".urbana.local" if testing subdomains locally
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
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=1, cast=int)),

    # Cookie-based JWT (cross-subdomain support)
    "AUTH_COOKIE": "access_token",
    "AUTH_COOKIE_REFRESH": "refresh_token",
    "AUTH_COOKIE_DOMAIN": COOKIE_DOMAIN,
    "AUTH_COOKIE_SECURE": COOKIE_SECURE,
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_SAMESITE": JWT_COOKIE_SAMESITE,
    "AUTH_COOKIE_PATH": "/",
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
    CORS_ALLOWED_ORIGINS = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "https://localhost:5173",
        "https://localhost:5174",
        "https://localhost:5175",
        "http://127.0.0.1:5173",
        "https://urbana.local:5172",
        "https://api.urbana.local:8000",
        "https://admin.urbana.local:5176",
        "https://auth.urbana.local:5173",
        "https://customer.urbana.local:5175",
        "https://designer.urbana.local:5174",
    ]
    CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS.copy()

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
EMAIL_PORT = 587
EMAIL_HOST_USER = "resend"
EMAIL_HOST_PASSWORD = config("RESEND_API_KEY")
EMAIL_USE_TLS = True

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