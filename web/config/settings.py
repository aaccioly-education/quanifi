import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse


BASE_DIR = Path(__file__).resolve().parents[2]


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def database_config():
    url = os.getenv("DATABASE_URL")
    if not url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        }

    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use postgres:// or postgresql://")
    options = dict(parse_qsl(parsed.query))
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
        "OPTIONS": options,
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-development-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "web.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.reports.middleware.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "web.config.urls"
WSGI_APPLICATION = "web.config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

DATABASES = {"default": database_config()}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TZ", "UTC")
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "var" / "static"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "reports:list"
LOGOUT_REDIRECT_URL = "login"
ADMIN_EMAIL = os.getenv("QUANIFI_ADMIN_EMAIL", "").strip().lower()
REPORT_INGESTION_TOKEN = os.getenv("REPORT_INGESTION_TOKEN", "")
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DJANGO_MAX_UPLOAD_BYTES", str(16 * 1024 * 1024)))

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
