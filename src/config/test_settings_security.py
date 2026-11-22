import pytest
from django.core.exceptions import ImproperlyConfigured

from config.security import DEV_SECRET_KEY, https_only_settings, secret_key

SECURE_KEYS = {
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "SECURE_PROXY_SSL_HEADER",
    "SECURE_HSTS_SECONDS",
}


def test_secret_key_uses_env_value():
    assert secret_key("real-key", debug=False) == "real-key"
    assert secret_key("real-key", debug=True) == "real-key"


@pytest.mark.parametrize("value", [None, ""])
def test_secret_key_missing_in_production_fails(value):
    with pytest.raises(ImproperlyConfigured):
        secret_key(value, debug=False)


@pytest.mark.parametrize("value", [None, ""])
def test_secret_key_missing_in_debug_uses_dev_key(value):
    assert secret_key(value, debug=True) == DEV_SECRET_KEY


def test_https_only_off_sets_nothing():
    assert https_only_settings(False) == {}


def test_https_only_on_sets_secure_settings():
    result = https_only_settings(True)

    assert set(result) == SECURE_KEYS
    assert result["SESSION_COOKIE_SECURE"] is True
    assert result["CSRF_COOKIE_SECURE"] is True
    assert result["SECURE_PROXY_SSL_HEADER"] == ("HTTP_X_FORWARDED_PROTO", "https")
    assert result["SECURE_HSTS_SECONDS"] > 0
    assert "SECURE_SSL_REDIRECT" not in result
