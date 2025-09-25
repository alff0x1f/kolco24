import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "wagtail.contrib.forms",
    "wagtail.contrib.redirects",
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "modelcluster",
    "taggit",
    "website",
    "mailer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
]

ROOT_URLCONF = "kolco24.urls"

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
    }
]

WSGI_APPLICATION = "kolco24.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

EMAIL_BACKEND = "mailer.backend.DbBackend"
AUTHENTICATION_BACKENDS = [
    "website.auth.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

REG_OPEN = True
CURRENT_YEAR = int(os.environ.get("KOLCO24_CURRENT_YEAR", "2024"))

YANDEX_NOTIFICATION_SECRET = os.environ.get("YANDEX_NOTIFICATION_SECRET", "secret")
YANDEX_WALLET = os.environ.get("YANDEX_WALLET", "4100000000000")
SBERBANK_INFO = {
    "phone": os.environ.get("SBERBANK_PHONE", "9000000000"),
    "name": os.environ.get("SBERBANK_NAME", "Иван Иванов"),
}
TINKOFF_INFO = {
    "phone": os.environ.get("TINKOFF_PHONE", "9000000000"),
    "name": os.environ.get("TINKOFF_NAME", "Иван Иванов"),
}
SBP_INFO = {
    "phone": os.environ.get("SBP_PHONE", "9000000000"),
    "name": os.environ.get("SBP_NAME", "Иван Иванов"),
}

GOOGLE_DOCS_KEY = os.environ.get("GOOGLE_DOCS_KEY", "")
PROTOCOL_DIR = str(MEDIA_ROOT / "protocols") + "/"
PROTOCOL_URL = "/media/protocols/"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
WAGTAIL_SITE_NAME = "Kolco24"
