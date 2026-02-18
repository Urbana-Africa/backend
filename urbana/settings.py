from pathlib import Path
from datetime import timedelta
from decouple import config


# =====================================================
# Paths & Environment
# =====================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ENV = config("ENV", default="dev").lower()  # dev | prod | staging

if ENV not in ["dev", "prod", "staging"]:
    raise ValueError(f"Invalid ENV value: {ENV}")

IS_PRODUCTION = ENV == "prod"


# =====================================================
# Hosts & Allowed Origins
# =====================================================

ALLOWED_HOSTS = ["api.urbanaafrica.com"]

if not IS_PRODUCTION:
    ALLOWED_HOSTS.extend(["127.0.0.1", "localhost", "api.urbana.local"])


# =====================================================
# Cookie & Security Domain (for shared cookies across subdomains)
# =====================================================

COOKIE_DOMAIN = ".urbanaafrica.com" if IS_PRODUCTION else None
SECURE_COOKIES = IS_PRODUCTION


# =====================================================
# Installed Applications
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

    # Project apps
    "apps.authentication",
    "apps.customers",
    "apps.designers",
    "apps.pay",
    "apps.core",
]


# =====================================================
# Middleware
# =====================================================

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =====================================================
# URLconf, ASGI, Auth
# =====================================================

ROOT_URLCONF = "urbana.urls"
WSGI_APPLICATION = "urbana.wsgi.application"
ASGI_APPLICATION = "urbana.asgi.application"
AUTH_USER_MODEL = "authentication.User"
LOGIN_URL = "/auth/login"


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

if ENV == "dev":
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
# REST Framework & Authentication
# =====================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
}


# =====================================================
# Simple JWT – Cookie-based tokens (shared across subdomains)
# =====================================================

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=1, cast=int)),

    # Cookie settings – these names are used in views
    "AUTH_COOKIE": "access_token",
    "AUTH_COOKIE_REFRESH": "refresh_token",

    "AUTH_COOKIE_DOMAIN": COOKIE_DOMAIN,               # ← Key fix: share across subdomains
    "AUTH_COOKIE_SECURE": SECURE_COOKIES,
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_SAMESITE": "Lax",                     # Allows cross-subdomain GET + top-level POST
    "AUTH_COOKIE_PATH": "/",
}


# =====================================================
# Session & CSRF – Shared across all *.urbanaafrica.com
# =====================================================

SESSION_COOKIE_DOMAIN = COOKIE_DOMAIN
SESSION_COOKIE_SECURE = SECURE_COOKIES
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_DOMAIN = COOKIE_DOMAIN
CSRF_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"


# =====================================================
# Production Security Headers
# =====================================================

if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


# =====================================================
# CORS – Allow all legitimate urbanaafrica.com subdomains + root
# =====================================================

CORS_ALLOW_CREDENTIALS = True

if IS_PRODUCTION:
    CORS_ALLOWED_ORIGIN_REGEXES = [
        r"^https://([a-z0-9-]+\.)*urbanaafrica\.com$",
    ]
    CSRF_TRUSTED_ORIGINS = [
        "https://urbanaafrica.com",
        "https://api.urbanaafrica.com",
        "https://auth.urbanaafrica.com",
        "https://customer.urbanaafrica.com",
        "https://designer.urbanaafrica.com",
        # Add more known frontends here if they appear later
    ]
else:
    CORS_ALLOWED_ORIGINS = [
         "https://urbana.local:5172",
        "https://api.urbana.local:8000",
        "https://auth.urbana.local:5173",
        "https://customer.urbana.local:5174",
        "https://designer.urbana.local:5175",
        "https://localhost:5173",
        "https://localhost:5174",
        "https://127.0.0.1:5173",
        "https://127.0.0.1:5174",
    ]
    CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS


# =====================================================
# Static & Media
# =====================================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# =====================================================
# Email
# =====================================================

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("SMTP_HOST")
EMAIL_PORT = config("SMTP_PORT", cast=int)
EMAIL_HOST_USER = config("SMTP_USER")
EMAIL_HOST_PASSWORD = config("SMTP_PASSWORD")
EMAIL_USE_TLS = True
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
# Third-party Keys
# =====================================================

GEMINI_SECRET_KEY = config("GEMINI_SECRET_KEY", default="")
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")


# =====================================================
# Internationalization
# =====================================================

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = False