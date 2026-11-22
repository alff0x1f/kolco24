from django.core.exceptions import ImproperlyConfigured

DEV_SECRET_KEY = "django-insecure-local-dev-only"
HSTS_SECONDS = 31536000


def secret_key(value, debug):
    if value:
        return value
    if debug:
        return DEV_SECRET_KEY
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG=False")


def https_only_settings(enabled):
    """The LAN server at the race start runs over plain HTTP, so this is opt-in."""
    if not enabled:
        return {}
    return {
        "SESSION_COOKIE_SECURE": True,
        "CSRF_COOKIE_SECURE": True,
        "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
        "SECURE_HSTS_SECONDS": HSTS_SECONDS,
    }
