import hashlib
import hmac
import time

import pytest
from django.db import IntegrityError
from django.test import RequestFactory

from apps.mobile.models import AppInstall
from apps.mobile.permissions import SignedAppPermission, _client_ip
from apps.mobile.signing import build_canonical, sha256_hex, sign, verify


def test_build_canonical_exact_format():
    canonical = build_canonical("get", "/app/race/1/legend/", "1700000000", b"")
    expected = "\n".join(
        [
            "GET",
            "/app/race/1/legend/",
            "1700000000",
            hashlib.sha256(b"").hexdigest(),
        ]
    )
    assert canonical == expected


def test_build_canonical_empty_body_hashes_empty_bytes():
    canonical = build_canonical("GET", "/x", "1", b"")
    assert canonical.split("\n")[-1] == sha256_hex(b"")
    assert canonical.endswith(
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_build_canonical_hashes_body():
    canonical = build_canonical("POST", "/x", "1", b"hello")
    assert canonical.split("\n")[-1] == hashlib.sha256(b"hello").hexdigest()


def test_sign_matches_hmac():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    expected = hmac.new(b"secret", canonical.encode(), hashlib.sha256).hexdigest()
    assert sign("secret", canonical) == expected


def test_verify_true_for_correct_signature():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    good_sig = sign("secret", canonical)
    assert verify("secret", canonical, good_sig) is True


def test_verify_false_for_tampered_signature():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    good_sig = sign("secret", canonical)
    tampered = "0" * len(good_sig)
    assert verify("secret", canonical, tampered) is False


def test_verify_false_for_wrong_secret():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    sig = sign("secret", canonical)
    assert verify("other-secret", canonical, sig) is False


def test_verify_false_for_changed_path():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    sig = sign("secret", canonical)
    tampered_canonical = build_canonical(
        "GET", "/app/race/2/legend/", "1700000000", b""
    )
    assert verify("secret", tampered_canonical, sig) is False


@pytest.mark.django_db
def test_appinstall_create_defaults():
    install = AppInstall.objects.create(install_id="abc-123")
    assert install.request_count == 0
    assert install.platform == ""
    assert install.app_version == ""
    assert install.first_seen is not None
    assert install.last_seen is not None


@pytest.mark.django_db
def test_appinstall_install_id_unique():
    AppInstall.objects.create(install_id="dup-id")
    with pytest.raises(IntegrityError):
        AppInstall.objects.create(install_id="dup-id")


# --- SignedAppPermission ----------------------------------------------------

SECRET = "test-secret"
PATH = "/app/race/1/legend/"


def _signed_get_request(secret=SECRET, ts=None, path=PATH, extra_headers=None):
    """Build a Django request with valid signed headers for a GET."""
    if ts is None:
        ts = str(int(time.time()))
    canonical = build_canonical("GET", path, ts, b"")
    sig = sign(secret, canonical)
    headers = {
        "X-App-Sig": sig,
        "X-App-Ts": ts,
        "X-Install-Id": "install-abc",
        "X-App-Platform": "ios",
        "X-App-Version": "1.4.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    return RequestFactory().get(path, headers=headers)


def test_permission_empty_secret_fails_closed(settings):
    settings.MOBILE_APP_SECRET = ""
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_missing_headers_false(settings):
    settings.MOBILE_APP_SECRET = SECRET
    request = RequestFactory().get(PATH)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_non_int_ts_false(settings):
    settings.MOBILE_APP_SECRET = SECRET
    request = _signed_get_request(ts="not-a-number")
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_expired_ts_false(settings):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300
    old_ts = str(int(time.time()) - 1000)
    request = _signed_get_request(ts=old_ts)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_bad_signature_false(settings):
    settings.MOBILE_APP_SECRET = SECRET
    request = _signed_get_request(secret="wrong-secret")
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_valid_true_and_stashes_meta(settings):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is True
    assert request.app_meta["install_id"] == "install-abc"
    assert request.app_meta["platform"] == "ios"
    assert request.app_meta["app_version"] == "1.4.0"
    assert "ip" in request.app_meta


def test_client_ip_prefers_forwarded_for():
    request = RequestFactory().get(
        PATH,
        headers={"X-Forwarded-For": "203.0.113.1, 10.0.0.1"},
        REMOTE_ADDR="10.0.0.2",
    )
    assert _client_ip(request) == "203.0.113.1"


def test_client_ip_falls_back_to_remote_addr():
    request = RequestFactory().get(PATH, REMOTE_ADDR="10.0.0.2")
    assert _client_ip(request) == "10.0.0.2"
