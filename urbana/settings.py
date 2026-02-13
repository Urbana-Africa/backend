"""
Django settings for Urbana project with subdomain support.
"""

import os
from pathlib import Path
from datetime import timedelta
from decouple import config, Csv

# -------------------------
# Paths
# -------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# -------------------------
# Basic environment flags
# -------------------------
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ENV = config("ENV", default="dev")  # 'dev' or 'prod'

# -------------------------
# Hosts & origins
# -------------------------
ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="127.0.0.1,localhost,api.urbana.local,auth.urbana.local,designer.urbana.local,customer.urbana.local,api.urbanaafrica.com",
    cast=Csv()
)

ALLOWED_FRONTEND = config("ALLOWED_FRONTEND", default="auth.urbana.local:5173")

# -------------------------
# Subdomain cookie settings
# -------------------------
if ENV == "prod":
    COOKIE_DOMAIN = ".urbanaafrica.com"
    SECURE_COOKIES = True
else:
    COOKIE_DOMAIN = ".urbana.local"
    SECURE_COOKIES = False

SESSION_COOKIE_DOMAIN = COOKIE_DOMAIN
SESSION_COOKIE_SECURE = SECURE_COOKIES
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "None" if SECURE_COOKIES else "Lax"

CSRF_COOKIE_DOMAIN = COOKIE_DOMAIN
CSRF_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "None" if SECURE_COOKIES else "Lax"

CSRF_USE_SESSIONS = False

# -------------------------
# CSRF Trusted Origins
# -------------------------
CSRF_TRUSTED_ORIGINS = [
    f"https://auth.urbana.local:5173",
    f"https://urbana.local:5172",
    f"https://designer.urbana.local:5174",
    f"https://customer.urbana.local:5175",
    f"https://api.urbana.local:8000",
]

if ENV == "prod":
    CSRF_TRUSTED_ORIGINS = [
        "https://urbanaafrica.com",
        "https://auth.urbanaafrica.com",
        "https://designer.urbanaafrica.com",
        "https://customer.urbanaafrica.com",
        "https://api.urbanaafrica.com",
    ]

# -------------------------
# CORS Settings
# -------------------------
CORS_ALLOWED_ORIGINS = CSRF_TRUSTED_ORIGINS
CORS_ALLOW_CREDENTIALS = True
CORS_EXPOSE_HEADERS = ["Content-Type", "Content-Disposition"]
CORS_ALLOWED_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]

# -------------------------
# Applications
# -------------------------
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.authentication",
    "corsheaders",
    "apps.customers",
    "apps.pay",
    "apps.designers",
    "apps.core",
    "django_apscheduler",
    "django_cleanup.apps.CleanupConfig",
    "django.contrib.humanize",
    "tinymce",
    "rest_framework",
    "knox",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "django_extensions",
    "channels",
    "oauth2_provider",
    "social_django",
    "drf_social_oauth2",
]

# -------------------------
# Middleware
# -------------------------
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

# -------------------------
# URL / auth
# -------------------------
ROOT_URLCONF = "urbana.urls"
WSGI_APPLICATION = "urbana.wsgi.application"
ASGI_APPLICATION = "urbana.asgi.application"
AUTH_USER_MODEL = "authentication.User"
LOGIN_URL = "/auth/login"

# Email Settings

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
RESEND_SMTP_PORT = 587
RESEND_SMTP_USERNAME = 'resend'
RESEND_SMTP_HOST = 'smtp.resend.com'
RESEND_API_KEY=config('RESEND_API_KEY')
SMTP_USER = config('SMTP_USER')
SMTP_HOST = config('SMTP_HOST')
SMTP_PASSWORD=config('SMTP_PASSWORD')
SMTP_PORT=config('SMTP_PORT')

GEMINI_SECRET_KEY=config('GEMINI_SECRET_KEY')


STRIPE_SECRET_KEY=config('STRIPE_SECRET_KEY')

# -------------------------
# Templates
# -------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
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

# -------------------------
# Databases
# -------------------------
if ENV == "dev":
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
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
            "OPTIONS": {"init_command": "SET sql_mode='STRICT_TRANS_TABLES'"},
        }
    }

# -------------------------
# Channels / Redis
# -------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [REDIS_URL]}}
}

# -------------------------
# REST Framework / JWT
# -------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=1, cast=int)),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_COOKIE": "access_token",
    "AUTH_COOKIE_REFRESH": "refresh_token",
    "AUTH_COOKIE_SECURE": SECURE_COOKIES,
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_PATH": "/",
    "AUTH_COOKIE_SAMESITE": "None" if SECURE_COOKIES else "Lax",
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticatedOrReadOnly",),
}

# -------------------------
# Static / Media
# -------------------------
STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "static")

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# -------------------------
# Misc
# -------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = False
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True
