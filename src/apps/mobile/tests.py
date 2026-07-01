import hashlib
import hmac
import time

import pytest
from django.db import IntegrityError
from django.db.models import Prefetch
from django.test import RequestFactory

from apps.mobile.models import AppAuthFailure, AppInstall
from apps.mobile.permissions import SignedAppPermission, _client_ip
from apps.mobile.signing import build_canonical, sha256_hex, sign, verify


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """Isolate DRF's per-process LocMemCache throttle counts between tests.

    The mobile-login/mobile-write ScopedRateThrottle keys on the client IP, and
    every test request comes from the same test IP — without a clear, counts
    accumulate across tests and trip a spurious 429.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


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


def test_verify_true_for_uppercase_signature():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    good_sig = sign("secret", canonical).upper()
    assert verify("secret", canonical, good_sig) is True


def test_verify_false_for_changed_path():
    canonical = build_canonical("GET", "/app/race/1/legend/", "1700000000", b"")
    sig = sign("secret", canonical)
    tampered_canonical = build_canonical(
        "GET", "/app/race/2/legend/", "1700000000", b""
    )
    assert verify("secret", tampered_canonical, sig) is False


@pytest.mark.django_db
def test_member_tag_serializer_field_set():
    from apps.mobile.serializers import MemberTagSerializer
    from website.models.tag import Tag

    tag = Tag.objects.create(number=42, nfc_uid="abc123")

    data = MemberTagSerializer(tag).data

    assert set(data.keys()) == {"number", "nfc_uid"}
    assert data["number"] == 42
    assert data["nfc_uid"] == "ABC123"


@pytest.mark.django_db
def test_legend_checkpoint_serializer_open_exposes_cleartext():
    from apps.mobile.serializers import LegendCheckpointSerializer
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Open ser", slug="open-ser")
    point = Checkpoint.objects.create(race=race, number=1, cost=4, description="tree")

    data = LegendCheckpointSerializer(point).data

    assert set(data.keys()) == {"id", "number", "type", "color", "cost", "description"}
    assert data["cost"] == 4
    assert data["description"] == "tree"
    assert data["color"] == ""
    assert "enc" not in data


@pytest.mark.django_db
def test_legend_checkpoint_serializer_locked_exposes_only_enc():
    from apps.mobile.crypto import unseal
    from apps.mobile.legend_crypto import seal_checkpoint
    from apps.mobile.serializers import LegendCheckpointSerializer
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Locked ser", slug="locked-ser")
    point = Checkpoint.objects.create(
        race=race,
        number=1,
        cost=4,
        description="tree",
        color="blue",
        is_legend_locked=True,
    )
    secret = seal_checkpoint(point)

    data = LegendCheckpointSerializer(point).data

    assert set(data.keys()) == {"id", "number", "type", "color", "enc"}
    assert data["color"] == "blue"
    assert "cost" not in data
    assert "description" not in data
    # the served enc blob decrypts (with the stored content_key) to the cleartext
    import json

    plaintext = unseal(
        bytes(secret.content_key), data["enc"], aad=str(point.id).encode()
    )
    assert json.loads(plaintext) == {"cost": 4, "description": "tree"}


@pytest.mark.django_db
def test_legend_checkpoint_serializer_locked_no_secret_fails_closed():
    """A locked КП with no CheckpointSecret must not leak cleartext (fail closed)."""
    from apps.mobile.serializers import LegendCheckpointSerializer
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="No secret race", slug="no-secret-race")
    # Create locked checkpoint via QuerySet.update to bypass the signal that
    # would normally call seal_checkpoint and create the secret.
    point = Checkpoint.objects.create(race=race, number=1, cost=9, description="hidden")
    Checkpoint.objects.filter(pk=point.pk).update(is_legend_locked=True)
    point.refresh_from_db()

    # Confirm no secret was created.
    assert not CheckpointSecret.objects.filter(checkpoint=point).exists()

    data = LegendCheckpointSerializer(point).data

    # Must not contain cleartext; only the identifier fields.
    assert "cost" not in data
    assert "description" not in data
    assert "enc" not in data
    assert set(data.keys()) == {"id", "number", "type", "color"}


@pytest.mark.django_db
def test_appinstall_create_defaults():
    install = AppInstall.objects.create(install_id="abc-123")
    assert install.request_count == 0
    assert install.platform == ""
    assert install.app_version == ""
    assert install.key_id == ""
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


def _signed_get_request(
    secret=SECRET, ts=None, path=PATH, extra_headers=None, key_id="test-v1"
):
    """Build a Django request with valid signed headers for a GET."""
    if ts is None:
        ts = str(int(time.time()))
    canonical = build_canonical("GET", path, ts, b"")
    sig = sign(secret, canonical)
    headers = {
        "X-App-Key-Id": key_id,
        "X-App-Sig": sig,
        "X-App-Ts": ts,
        "X-Install-Id": "install-abc",
        "X-App-Platform": "ios",
        "X-App-Version": "1.4.0",
    }
    if key_id is None:
        del headers["X-App-Key-Id"]
    if extra_headers:
        headers.update(extra_headers)
    return RequestFactory().get(path, headers=headers)


def test_permission_empty_keys_fails_closed(settings):
    settings.MOBILE_APP_KEYS = {}
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_unset_keys_fails_closed(settings):
    # The getattr(..., {}) or {} unset/None path also fails closed.
    settings.MOBILE_APP_KEYS = None
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_unknown_key_id_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(key_id="nope-v9")
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_missing_key_id_header_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(key_id=None)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_missing_headers_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = RequestFactory().get(PATH)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_empty_string_secret_fails_closed(settings):
    # keys.get(key_id) returns "" → falsy → same neutral 403 as unknown key
    settings.MOBILE_APP_KEYS = {"test-v1": ""}
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is False


@pytest.mark.parametrize(
    "drop_header", ["X-App-Key-Id", "X-App-Sig", "X-App-Ts", "X-Install-Id"]
)
def test_permission_each_required_header_individually_false(settings, drop_header):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    ts = str(int(time.time()))
    canonical = build_canonical("GET", PATH, ts, b"")
    headers = {
        "X-App-Key-Id": "test-v1",
        "X-App-Sig": sign(SECRET, canonical),
        "X-App-Ts": ts,
        "X-Install-Id": "install-abc",
    }
    del headers[drop_header]
    request = RequestFactory().get(PATH, headers=headers)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_non_int_ts_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(ts="not-a-number")
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_expired_ts_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    old_ts = str(int(time.time()) - 1000)
    request = _signed_get_request(ts=old_ts)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_future_ts_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    future_ts = str(int(time.time()) + 1000)
    request = _signed_get_request(ts=future_ts)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_huge_ts_returns_false_not_500(settings):
    # A very large integer timestamp is simply outside the replay window and
    # must return False cleanly (Python integers are arbitrary-precision; no
    # overflow risk).
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    huge_ts = str(10**400)
    request = _signed_get_request(ts=huge_ts)
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_bad_signature_false(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(secret="wrong-secret")
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_valid_true_and_stashes_meta(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is True
    assert request.app_meta["install_id"] == "install-abc"
    assert request.app_meta["platform"] == "ios"
    assert request.app_meta["app_version"] == "1.4.0"
    assert request.app_meta["key_id"] == "test-v1"
    assert "ip" in request.app_meta


# Task 3: the permission is tested in isolation here (RequestFactory + the
# permission object). The DB-row side effects are covered by Task 4's view tests.
def test_permission_no_keys_stashes_reason(settings):
    settings.MOBILE_APP_KEYS = {}
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is False
    assert request.app_denial["reason"] == "no_keys"
    assert request.app_denial["key_id"] == ""


def test_permission_missing_headers_stashes_reason_and_empty_key_id(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = RequestFactory().get(PATH)
    assert SignedAppPermission().has_permission(request, None) is False
    # X-App-Key-Id absent → coerced to "" (no TypeError on the length-clamp).
    assert request.app_denial["reason"] == "missing_headers"
    assert request.app_denial["key_id"] == ""


def test_permission_missing_headers_with_key_id_present_stashes_blank_key_id(settings):
    # key_id is provided but sig is missing — key_id must still be normalised to ""
    # so an attacker rotating X-App-Key-Id doesn't force a row per request.
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    ts = str(int(time.time()))
    request = RequestFactory().get(
        PATH,
        headers={"X-App-Key-Id": "fake-key", "X-App-Ts": ts, "X-Install-Id": "x"},
    )
    assert SignedAppPermission().has_permission(request, None) is False
    assert request.app_denial["reason"] == "missing_headers"
    assert request.app_denial["key_id"] == ""


def test_permission_unknown_key_stashes_reason_with_blank_key_id(settings):
    # key_id is normalised to "" so different fake key-ids aggregate to one row.
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(key_id="nope-v9")
    assert SignedAppPermission().has_permission(request, None) is False
    assert request.app_denial["reason"] == "unknown_key"
    assert request.app_denial["key_id"] == ""


def test_permission_bad_ts_stashes_reason(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(ts="not-a-number")
    assert SignedAppPermission().has_permission(request, None) is False
    assert request.app_denial["reason"] == "bad_ts"


def test_permission_expired_ts_stashes_reason(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    request = _signed_get_request(ts=str(int(time.time()) - 1000))
    assert SignedAppPermission().has_permission(request, None) is False
    assert request.app_denial["reason"] == "expired_ts"


def test_permission_bad_sig_stashes_reason(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    request = _signed_get_request(secret="wrong-secret")
    assert SignedAppPermission().has_permission(request, None) is False
    assert request.app_denial["reason"] == "bad_sig"
    assert request.app_denial["key_id"] == "test-v1"
    assert request.app_denial["path"] == PATH
    assert request.app_denial["install"] == "install-abc"


def test_permission_success_does_not_stash_denial(settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is True
    assert not hasattr(request, "app_denial")


def test_permission_two_active_keys_both_verify(settings):
    # Rotation-overlap proof: two keys active at once, each request signed with
    # its own paired secret + key-id verifies.
    settings.MOBILE_APP_KEYS = {"android-v1": "secret-1", "ios-v1": "secret-2"}
    settings.MOBILE_APP_TS_WINDOW = 300

    req1 = _signed_get_request(secret="secret-1", key_id="android-v1")
    assert SignedAppPermission().has_permission(req1, None) is True
    assert req1.app_meta["key_id"] == "android-v1"

    req2 = _signed_get_request(secret="secret-2", key_id="ios-v1")
    assert SignedAppPermission().has_permission(req2, None) is True
    assert req2.app_meta["key_id"] == "ios-v1"


def test_permission_valid_key_id_wrong_secret_false(settings):
    # A known key-id but a signature made with the wrong secret must fail.
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    request = _signed_get_request(secret="wrong-secret", key_id="test-v1")
    assert SignedAppPermission().has_permission(request, None) is False


def test_client_ip_uses_x_real_ip_when_set():
    # Production path: nginx resolves the real IP and emits X-Real-IP.
    request = RequestFactory().get(
        PATH,
        headers={"X-Real-IP": "203.0.113.5", "X-Forwarded-For": "1.2.3.4"},
        REMOTE_ADDR="10.0.0.1",
    )
    assert _client_ip(request) == "203.0.113.5"


def test_client_ip_invalid_x_real_ip_falls_back_to_forwarded_for():
    # Invalid X-Real-IP should not be trusted; fall through to XFF.
    request = RequestFactory().get(
        PATH,
        headers={"X-Real-IP": "not-an-ip", "X-Forwarded-For": "10.0.0.1, 10.0.0.2"},
        REMOTE_ADDR="10.0.0.3",
    )
    assert _client_ip(request) == "10.0.0.2"


def test_client_ip_uses_last_forwarded_for_entry():
    # Last entry is the one nginx appended ($remote_addr); the first can be spoofed.
    request = RequestFactory().get(
        PATH,
        headers={"X-Forwarded-For": "203.0.113.1, 10.0.0.1"},
        REMOTE_ADDR="10.0.0.2",
    )
    assert _client_ip(request) == "10.0.0.1"


def test_client_ip_spoofed_first_entry_ignored():
    # Attacker sends a fake first entry; nginx appends real IP at the end.
    request = RequestFactory().get(
        PATH,
        headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.99"},
        REMOTE_ADDR="10.0.0.2",
    )
    assert _client_ip(request) == "203.0.113.99"


def test_client_ip_falls_back_to_remote_addr():
    request = RequestFactory().get(PATH, REMOTE_ADDR="10.0.0.2")
    assert _client_ip(request) == "10.0.0.2"


def test_client_ip_invalid_forwarded_for_falls_back_to_remote_addr():
    request = RequestFactory().get(
        PATH,
        headers={"X-Forwarded-For": "not-an-ip"},
        REMOTE_ADDR="10.0.0.2",
    )
    assert _client_ip(request) == "10.0.0.2"


# --- LegendView request-level ----------------------------------------------


def _signed_headers(method, path, secret, body=b"", key_id="test-v1"):
    """Build signed request headers mirroring the client side."""
    ts = str(int(time.time()))
    canonical = build_canonical(method, path, ts, body)
    sig = sign(secret, canonical)
    headers = {
        "HTTP_X_APP_KEY_ID": key_id,
        "HTTP_X_APP_SIG": sig,
        "HTTP_X_APP_TS": ts,
        "HTTP_X_INSTALL_ID": "install-abc",
        "HTTP_X_APP_PLATFORM": "ios",
        "HTTP_X_APP_VERSION": "1.4.0",
    }
    if key_id is None:
        del headers["HTTP_X_APP_KEY_ID"]
    return headers


@pytest.mark.django_db
def test_legend_valid_signature_returns_200_with_fields_and_order(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Test race", slug="test-race")
    Checkpoint.objects.create(race=race, number=3, cost=2, description="third")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    Checkpoint.objects.create(race=race, number=2, cost=1, description="second")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert data["race"] == race.id
    assert data["total_cost"] == 4  # 2 + 1 + 1 over all non-hidden КП
    assert data["tags"] == []
    assert [c["number"] for c in data["checkpoints"]] == [1, 2, 3]
    first = data["checkpoints"][0]
    assert set(first.keys()) == {"id", "number", "cost", "type", "color", "description"}
    assert first["type"] == "kp"
    assert first["description"] == "first"
    assert first["color"] == ""


# --- End-to-end gate + stats (request-level) -------------------------------


@pytest.fixture
def race_with_checkpoints(db):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="E2E race", slug="e2e-race")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    return race


@pytest.mark.django_db
def test_legend_no_headers_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    response = client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_wrong_signature_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    headers = _signed_headers("GET", path, "wrong-secret")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_expired_ts_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    old_ts = str(int(time.time()) - 1000)
    canonical = build_canonical("GET", path, old_ts, b"")
    sig = sign(SECRET, canonical)
    headers = {
        "HTTP_X_APP_KEY_ID": "test-v1",
        "HTTP_X_APP_SIG": sig,
        "HTTP_X_APP_TS": old_ts,
        "HTTP_X_INSTALL_ID": "install-abc",
        "HTTP_X_APP_PLATFORM": "ios",
        "HTTP_X_APP_VERSION": "1.4.0",
    }
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_empty_keys_fails_closed(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {}
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    # even a "correctly" signed request (against an empty map) must be rejected
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert AppAuthFailure.objects.filter(reason="no_keys").exists()


@pytest.mark.django_db
def test_legend_valid_sig_nonexistent_race_returns_404(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = "/app/race/999999/legend/"
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path, **headers)
    assert response.status_code == 404


@pytest.mark.django_db
def test_legend_unpublished_race_returns_404(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race = Race.objects.create(name="Unpub", slug="unpub-legend", is_published=False)
    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_legend_records_appinstall_and_increments(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200

    install = AppInstall.objects.get(install_id="install-abc")
    assert install.request_count == 1
    assert install.platform == "ios"
    assert install.app_version == "1.4.0"
    first_last_seen = install.last_seen

    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200

    install.refresh_from_db()
    assert install.request_count == 2
    assert install.last_seen >= first_last_seen
    assert AppInstall.objects.filter(install_id="install-abc").count() == 1


@pytest.mark.django_db
def test_legend_always_served_for_published_race(client, settings):
    """A published race always serves its legend — there is no race-level gate."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Served race", slug="served-race")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="open cp")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert data["race"] == race.id
    numbers = [c["number"] for c in data["checkpoints"]]
    assert numbers == [1]


@pytest.mark.django_db
def test_legend_excludes_hidden_checkpoints(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Hidden race", slug="hidden-race")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    hidden = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="hidden cp", type="hidden"
    )
    CheckpointTag.objects.create(checkpoint=hidden, nfc_uid="HIDDEN:TAG")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    numbers = [c["number"] for c in data["checkpoints"]]
    assert numbers == [1]
    assert data["tags"] == []


@pytest.mark.django_db
def test_legend_response_carries_etag(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    etag = response["ETag"]
    assert etag.startswith('"') and etag.endswith('"')


@pytest.mark.django_db
def test_legend_if_none_match_returns_304_empty_body(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    etag = first["ETag"]

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = etag
    second = client.get(path, **headers)

    assert second.status_code == 304
    assert second["ETag"] == etag
    assert second.content == b""
    assert AppInstall.objects.get(install_id="install-abc").request_count == 2


@pytest.mark.django_db
def test_legend_stale_if_none_match_returns_200_with_new_etag(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Stale legend", slug="stale-legend")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="first")

    path = f"/app/race/{race.id}/legend/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    old_etag = first["ETag"]

    Checkpoint.objects.create(race=race, number=2, cost=1, description="second")

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = old_etag
    second = client.get(path, **headers)

    assert second.status_code == 200
    assert second["ETag"] != old_etag


@pytest.mark.django_db
def test_legend_etag_changes_when_color_edited(client, settings):
    """A color edit bumps Checkpoint.updated_at, so the legend ETag moves."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Color etag", slug="color-etag")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")

    path = f"/app/race/{race.id}/legend/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    assert first.status_code == 200
    old_etag = first["ETag"]

    # A plain save() (no update_fields) lets auto_now bump updated_at.
    cp.color = "purple"
    cp.save()

    second = client.get(path, **_signed_headers("GET", path, SECRET))
    assert second.status_code == 200
    assert second["ETag"] != old_etag
    assert second.json()["checkpoints"][0]["color"] == "purple"


@pytest.mark.django_db
def test_legend_locked_cp_serves_enc_not_cleartext_open_serves_cleartext(
    client, settings
):
    """Mixed legend: a locked КП exposes only ``enc``; an open КП stays cleartext."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Mixed legend", slug="mixed-legend")
    Checkpoint.objects.create(
        race=race,
        number=1,
        cost=4,
        description="secret tree",
        color="red",
        is_legend_locked=True,
    )
    Checkpoint.objects.create(
        race=race, number=2, cost=2, description="open spot", color="green"
    )

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    locked, open_cp = data["checkpoints"]

    assert set(locked.keys()) == {"id", "number", "type", "color", "enc"}
    assert set(locked["enc"].keys()) == {"iv", "ct"}
    assert locked["color"] == "red"
    assert "cost" not in locked
    assert "description" not in locked

    assert set(open_cp.keys()) == {
        "id",
        "number",
        "type",
        "color",
        "cost",
        "description",
    }
    assert open_cp["color"] == "green"
    assert open_cp["description"] == "open spot"

    # the locked КП's cleartext never appears anywhere in the serialized body
    body = response.content.decode()
    assert "secret tree" not in body
    # ...but the open КП's cleartext does
    assert "open spot" in body

    # total_cost folds in the locked КП's cost (4 + 2) as an aggregate only —
    # the per-КП locked cost still isn't exposed (no "cost" key on the locked КП).
    assert data["total_cost"] == 6


@pytest.mark.django_db
def test_legend_total_cost_sums_non_hidden_only(client, settings):
    """total_cost = Σ cost over open + locked КП, excluding hidden КП."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Totals race", slug="totals-race")
    Checkpoint.objects.create(race=race, number=1, cost=3, description="open")
    Checkpoint.objects.create(
        race=race, number=2, cost=5, description="locked", is_legend_locked=True
    )
    Checkpoint.objects.create(
        race=race, number=3, cost=99, description="hidden", type="hidden"
    )

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    # 3 (open) + 5 (locked); the hidden КП's cost of 99 is excluded.
    assert response.json()["total_cost"] == 8


@pytest.mark.django_db
def test_legend_total_cost_empty_race_is_zero(client, settings):
    """An empty race yields total_cost == 0 (Sum None coalesced to 0)."""
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Empty race", slug="empty-totals-race")
    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["total_cost"] == 0


@pytest.mark.django_db
def test_legend_end_to_end_scan_code_decrypts_locked_checkpoint(client, settings):
    """Full offline flow: read code → bid → HKDF → bundle → content_key → enc."""
    import base64
    import hashlib
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="E2E decrypt", slug="e2e-decrypt")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="столб у воды", is_legend_locked=True
    )
    # The tag's empty unlocks falls back to [point]; signals seal + build bundle.
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")
    tag.refresh_from_db()
    code = bytes(tag.code)  # what is written into the physical NFC tag's memory

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200
    data = response.json()

    # 1. locate the tag by the bid computed from the scanned code; its
    #    `checkpoint_id` identifies which КП was physically scanned (always present)
    bid = hashlib.sha256(code).hexdigest()[:16]
    tag_entry = next(t for t in data["tags"] if t["bid"] == bid)
    assert tag_entry["check_method"] == "offline"
    assert tag_entry["checkpoint_id"] == cp.id

    # 2. HKDF(code) decrypts the tag's bundle → {cp_id: content_key}
    keys = json.loads(
        unseal(
            derive_wrap_key(code),
            {"iv": tag_entry["iv"], "ct": tag_entry["ct"]},
            aad=bid.encode(),
        )
    )
    content_key = base64.b64decode(keys[str(cp.id)])

    # 3. the content_key decrypts the locked КП's enc blob → original cleartext
    locked = data["checkpoints"][0]
    plaintext = json.loads(unseal(content_key, locked["enc"], aad=str(cp.id).encode()))
    assert plaintext == {"cost": 4, "description": "столб у воды"}


@pytest.mark.django_db
def test_legend_tags_include_open_checkpoint_tag_with_checkpoint_id_no_iv_ct(
    client, settings
):
    """An open-КП tag rides in `tags` with `checkpoint_id` for identity, no iv/ct."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Open tag", slug="open-tag")
    cp = Checkpoint.objects.create(race=race, number=1, cost=2, description="open spot")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )
    tag.refresh_from_db()

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert len(data["tags"]) == 1
    entry = data["tags"][0]
    assert set(entry.keys()) == {"bid", "checkpoint_id", "iv", "ct", "check_method"}
    assert "point" not in entry
    assert entry["checkpoint_id"] == cp.id
    assert entry["check_method"] == "offline"
    assert entry["bid"] == tag.bid
    assert entry["iv"] is None
    assert entry["ct"] is None


@pytest.mark.django_db
def test_legend_tags_exclude_unbuilt_tag_with_empty_bid(client, settings):
    """A tag with no code/bid (bid="", created bypassing signals) is excluded."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Unbuilt tag", slug="unbuilt-tag")
    cp = Checkpoint.objects.create(race=race, number=1, cost=2, description="open spot")
    # Bypass the build_bundle signal so the row keeps its bid="" default.
    CheckpointTag.objects.bulk_create(
        [CheckpointTag(checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline")]
    )
    assert CheckpointTag.objects.filter(bid="").count() == 1

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["tags"] == []


@pytest.mark.django_db
def test_legend_etag_changes_when_tag_edited_and_304_with_new_etag(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Tag etag", slug="tag-etag")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )

    path = f"/app/race/{race.id}/legend/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    old_etag = first["ETag"]

    tag.check_method = "online"
    tag.save()

    second = client.get(path, **_signed_headers("GET", path, SECRET))
    new_etag = second["ETag"]
    assert new_etag != old_etag

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = new_etag
    third = client.get(path, **headers)
    assert third.status_code == 304
    assert third["ETag"] == new_etag


@pytest.mark.django_db
def test_legend_etag_changes_when_tag_edited_with_update_fields(client, settings):
    """save(update_fields=[..., "updated_at"]) on CheckpointTag must move the ETag.

    CLAUDE.md mandates that save(update_fields=...) on auto_now models must
    include "updated_at"; this verifies that discipline keeps the legend
    fingerprint fresh (i.e. omitting "updated_at" from update_fields would
    silently stale the ETag, and this test would catch it).
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Tag etag uf", slug="tag-etag-uf")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )

    path = f"/app/race/{race.id}/legend/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    old_etag = first["ETag"]

    tag.check_method = "online"
    tag.save(update_fields=["check_method", "updated_at"])

    second = client.get(path, **_signed_headers("GET", path, SECRET))
    assert second["ETag"] != old_etag


@pytest.mark.django_db
def test_legend_build_independent_same_etag_and_body_across_key_ids(client, settings):
    """Two builds (different secrets) get the **same** legend ETag + body.

    The legend is build-independent now: the stored ciphertext/bundles do not
    depend on the per-build secret, so an app update that rotates X-App-Key-Id
    sees an unchanged ETag (no spurious re-fetch).
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"build-a": "secret-a", "build-b": "secret-b"}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Per build", slug="per-build-legend")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="first", is_legend_locked=True
    )
    CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )

    path = f"/app/race/{race.id}/legend/"
    resp_a = client.get(
        path, **_signed_headers("GET", path, "secret-a", key_id="build-a")
    )
    resp_b = client.get(
        path, **_signed_headers("GET", path, "secret-b", key_id="build-b")
    )

    assert resp_a["ETag"] == resp_b["ETag"]
    assert resp_a.json() == resp_b.json()

    # An If-None-Match from build-a's ETag 304s for build-b too.
    headers = _signed_headers("GET", path, "secret-b", key_id="build-b")
    headers["HTTP_IF_NONE_MATCH"] = resp_a["ETag"]
    cross = client.get(path, **headers)
    assert cross.status_code == 304


@pytest.mark.django_db
def test_legend_tampered_query_string_returns_403(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    # Signature covers the bare path; appending a query string must be rejected
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path + "?foo=bar", **headers)
    assert response.status_code == 403


# --- RaceListView request-level --------------------------------------------

RACES_PATH = "/app/races/"
RACE_FIELDS = {
    "id",
    "name",
    "slug",
    "date",
    "date_end",
    "place",
    "reg_status",
}


@pytest.mark.django_db
def test_races_valid_signature_returns_published_in_date_order(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    Race.objects.create(name="Old", slug="old", date="2024-01-01")
    Race.objects.create(name="New", slug="new", date="2026-01-01")
    Race.objects.create(name="Mid", slug="mid", date="2025-01-01")
    Race.objects.create(
        name="Hidden", slug="hidden", date="2027-01-01", is_published=False
    )

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert list(data.keys()) == ["races"]
    slugs = [r["slug"] for r in data["races"]]
    assert slugs == ["new", "mid", "old"]  # -date order, hidden excluded
    assert set(data["races"][0].keys()) == RACE_FIELDS


@pytest.mark.django_db
def test_races_empty_when_no_published(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    Race.objects.create(name="Hidden", slug="hidden", is_published=False)

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    assert response.json() == {"races": []}


@pytest.mark.django_db
def test_races_no_headers_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    response = client.get(RACES_PATH)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_races_wrong_signature_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    headers = _signed_headers("GET", RACES_PATH, "wrong-secret")
    response = client.get(RACES_PATH, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_stats_write_failure_does_not_break_response(
    client, settings, race_with_checkpoints, monkeypatch
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    def boom(*args, **kwargs):
        raise IntegrityError("simulated stats write failure")

    monkeypatch.setattr(AppInstall.objects, "update_or_create", boom)

    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["race"] == race_with_checkpoints.id
    assert not AppInstall.objects.filter(install_id="install-abc").exists()


@pytest.mark.django_db
def test_races_records_appinstall(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    install = AppInstall.objects.get(install_id="install-abc")
    assert install.request_count == 1


@pytest.mark.django_db
def test_races_stats_write_failure_does_not_break_response(
    client, settings, monkeypatch
):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    Race.objects.create(name="Published", slug="published-race", is_published=True)

    def boom(*args, **kwargs):
        raise IntegrityError("simulated stats write failure")

    monkeypatch.setattr(AppInstall.objects, "update_or_create", boom)

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    assert len(response.json()["races"]) == 1
    assert not AppInstall.objects.filter(install_id="install-abc").exists()


@pytest.mark.django_db
def test_races_tampered_query_string_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    # Signature covers the bare path; appending a query string must be rejected
    headers = _signed_headers("GET", RACES_PATH, SECRET)
    response = client.get(RACES_PATH + "?page=2", **headers)
    assert response.status_code == 403


@pytest.mark.django_db
def test_races_response_carries_etag(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    Race.objects.create(name="Published", slug="published-race", is_published=True)

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    etag = response["ETag"]
    assert etag.startswith('"') and etag.endswith('"')


@pytest.mark.django_db
def test_races_if_none_match_returns_304_empty_body(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    Race.objects.create(name="Published", slug="published-race")

    first = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))
    etag = first["ETag"]

    headers = _signed_headers("GET", RACES_PATH, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = etag
    second = client.get(RACES_PATH, **headers)

    assert second.status_code == 304
    assert second["ETag"] == etag
    assert second.content == b""
    assert AppInstall.objects.get(install_id="install-abc").request_count == 2


@pytest.mark.django_db
def test_races_stale_if_none_match_returns_200_with_new_etag(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Published", slug="published-race")

    first = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))
    old_etag = first["ETag"]

    race.name = "Renamed"
    race.save()

    headers = _signed_headers("GET", RACES_PATH, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = old_etag
    second = client.get(RACES_PATH, **headers)

    assert second.status_code == 200
    assert second["ETag"] != old_etag
    assert second.json()["races"][0]["name"] == "Renamed"


@pytest.mark.django_db
def test_races_empty_list_carries_etag_and_304(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    first = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))
    assert first.status_code == 200
    assert first.json() == {"races": []}
    etag = first["ETag"]

    headers = _signed_headers("GET", RACES_PATH, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = etag
    second = client.get(RACES_PATH, **headers)

    assert second.status_code == 304
    assert second["ETag"] == etag


# --- races_version fingerprint -----------------------------------------------


@pytest.mark.django_db
def test_races_version_stable_with_no_published_races():
    from apps.mobile.versioning import races_version
    from website.models.race import Race

    Race.objects.create(name="Hidden", slug="hidden", is_published=False)
    assert races_version() == races_version()


@pytest.mark.django_db
def test_races_version_changes_on_publish_and_unpublish():
    from apps.mobile.versioning import races_version
    from website.models.race import Race

    race = Race.objects.create(name="Race", slug="race", is_published=False)
    before = races_version()

    race.is_published = True
    race.save()
    published = races_version()
    assert published != before

    race.is_published = False
    race.save()
    assert races_version() != published


@pytest.mark.django_db
def test_races_version_changes_when_published_race_edited():
    from apps.mobile.versioning import races_version
    from website.models.race import Race

    race = Race.objects.create(name="Race", slug="race")
    before = races_version()

    race.place = "New place"
    race.save()
    assert races_version() != before


@pytest.mark.django_db
def test_races_version_changes_on_reg_status_update_fields():
    """The SOLD_OUT flip uses update_fields and must still move the version."""
    from apps.mobile.versioning import races_version
    from website.models.race import Race, RegStatus

    race = Race.objects.create(name="Race", slug="race")
    before = races_version()

    race.reg_status = RegStatus.SOLD_OUT
    race.save(update_fields=["reg_status", "updated_at"])
    assert races_version() != before


@pytest.mark.django_db
def test_races_version_ignores_unpublished_race_edit():
    from apps.mobile.versioning import races_version
    from website.models.race import Race

    Race.objects.create(name="Published", slug="published")
    hidden = Race.objects.create(name="Hidden", slug="hidden", is_published=False)
    before = races_version()

    hidden.place = "Elsewhere"
    hidden.save()
    assert races_version() == before


# --- teams_version fingerprint ----------------------------------------------


def _make_race_with_category(name="Versioned race", slug="versioned-race"):
    from website.models.race import Category, Race

    race = Race.objects.create(name=name, slug=slug)
    category = Category.objects.create(code="open", name="Open", race=race)
    return race, category


@pytest.mark.django_db
def test_teams_version_stable_for_race_with_no_teams():
    from apps.mobile.versioning import teams_version

    race, _ = _make_race_with_category()
    first = teams_version(race.id)
    second = teams_version(race.id)
    assert first == second
    assert first  # non-empty


@pytest.mark.django_db
def test_teams_version_changes_when_team_added(django_user_model):
    from apps.mobile.versioning import teams_version
    from website.models.models import Team

    race, category = _make_race_with_category()
    user = django_user_model.objects.create_user(
        username="owner1", email="o1@example.com", password="x"
    )
    before = teams_version(race.id)
    Team.objects.create(owner=user, category2=category, teamname="Alpha")
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_changes_when_team_edited(django_user_model):
    from apps.mobile.versioning import teams_version
    from website.models.models import Team

    race, category = _make_race_with_category()
    user = django_user_model.objects.create_user(
        username="owner2", email="o2@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Beta")
    before = teams_version(race.id)
    team.teamname = "Beta renamed"
    team.save()
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_changes_when_athlet_renamed(django_user_model):
    from apps.mobile.versioning import teams_version
    from website.models.models import Athlet, Team

    race, category = _make_race_with_category()
    user = django_user_model.objects.create_user(
        username="owner3", email="o3@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Gamma")
    athlet = Athlet.objects.create(
        owner=user, team=team, name="Runner", number_in_team=1
    )
    before = teams_version(race.id)
    athlet.name = "Runner renamed"
    athlet.save()
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_changes_when_category_renamed():
    from apps.mobile.versioning import teams_version

    race, category = _make_race_with_category()
    before = teams_version(race.id)
    category.name = "Open renamed"
    category.save()
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_changes_when_category_added():
    from apps.mobile.versioning import teams_version
    from website.models.race import Category

    race, _ = _make_race_with_category()
    before = teams_version(race.id)
    Category.objects.create(code="sport", name="Sport", race=race)
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_changes_when_category_deleted():
    from apps.mobile.versioning import teams_version
    from website.models.race import Category

    race, _ = _make_race_with_category()
    extra = Category.objects.create(code="sport", name="Sport", race=race)
    before = teams_version(race.id)
    extra.delete()
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_changes_when_category_deactivated():
    from apps.mobile.versioning import teams_version
    from website.models.race import Category

    race, _ = _make_race_with_category()
    extra = Category.objects.create(code="sport", name="Sport", race=race)
    before = teams_version(race.id)
    extra.is_active = False
    extra.save()
    after = teams_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_teams_version_stable_for_race_with_zero_categories():
    from apps.mobile.versioning import teams_version
    from website.models.race import Race

    race = Race.objects.create(name="No categories", slug="no-categories")
    first = teams_version(race.id)
    second = teams_version(race.id)
    assert first == second
    assert first  # non-empty, no crash on None aggregate


# --- legend_version fingerprint ---------------------------------------------


@pytest.mark.django_db
def test_legend_version_stable_for_empty_race():
    from apps.mobile.versioning import legend_version
    from website.models.race import Race

    race = Race.objects.create(name="Empty legend", slug="empty-legend")
    first = legend_version(race.id)
    second = legend_version(race.id)
    assert first == second
    assert first  # non-empty


@pytest.mark.django_db
def test_legend_version_changes_when_checkpoint_description_edited():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Edit legend", slug="edit-legend")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="old")
    before = legend_version(race.id)
    cp.description = "new"
    cp.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_checkpoint_added():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Add legend", slug="add-legend")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    before = legend_version(race.id)
    Checkpoint.objects.create(race=race, number=2, cost=1, description="second")
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_checkpoint_removed():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Remove legend", slug="remove-legend")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    Checkpoint.objects.create(race=race, number=2, cost=1, description="second")
    before = legend_version(race.id)
    cp.delete()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_kp_flips_to_hidden():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Kp to hidden", slug="kp-to-hidden")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    before = legend_version(race.id)
    cp.type = "hidden"
    cp.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_hidden_flips_to_kp():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Hidden to kp", slug="hidden-to-kp")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="cp", type="hidden"
    )
    before = legend_version(race.id)
    cp.type = "kp"
    cp.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_unchanged_when_hidden_checkpoint_edited():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Hidden edit", slug="hidden-edit")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    hidden = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="hidden", type="hidden"
    )
    before = legend_version(race.id)
    hidden.description = "hidden edited"
    hidden.save()
    after = legend_version(race.id)
    assert before == after


@pytest.mark.django_db
def test_legend_version_changes_when_tag_check_method_edited():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag edit", slug="tag-edit")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="AA:BB", check_method="offline"
    )
    before = legend_version(race.id)
    tag.check_method = "online"
    tag.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_tag_added_and_removed():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag add", slug="tag-add")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    before = legend_version(race.id)
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="AA:BB", check_method="offline"
    )
    after_add = legend_version(race.id)
    assert before != after_add
    tag.delete()
    after_remove = legend_version(race.id)
    assert after_add != after_remove


@pytest.mark.django_db
def test_legend_version_changes_when_open_checkpoint_tag_added():
    """Adding a tag to an *open* (unlocked) КП moves the fingerprint.

    Guards that ``legend_version``'s ``CheckpointTag`` aggregate spans open-КП
    tags (not just locked ones), so the ``tags`` body — which now carries one
    entry per tag incl. open КП — can never go stale.
    """
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Open tag", slug="open-tag")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="open cp")
    assert cp.is_legend_locked is False  # this is an OPEN КП

    before = legend_version(race.id)
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="AA:BB", check_method="offline"
    )
    after_add = legend_version(race.id)
    assert before != after_add
    tag.delete()
    assert legend_version(race.id) != after_add


@pytest.mark.django_db
def test_legend_version_unchanged_when_tag_on_hidden_checkpoint_added():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag hidden", slug="tag-hidden")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    hidden = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="hidden", type="hidden"
    )
    before = legend_version(race.id)
    CheckpointTag.objects.create(
        checkpoint=hidden, nfc_uid="AA:BB", check_method="offline"
    )
    after = legend_version(race.id)
    assert before == after


@pytest.mark.django_db
def test_legend_version_changes_on_lock_toggle_and_reseal():
    """A lock toggle creates/deletes a CheckpointSecret → the version moves.

    Folds the ``CheckpointSecret`` aggregate into the fingerprint (the
    build-independent replacement for the old ``key_id`` term).
    """
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Lock version", slug="lock-version")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    before = legend_version(race.id)

    cp.is_legend_locked = True
    cp.save()  # signal seals → secret appears
    after_lock = legend_version(race.id)
    assert after_lock != before

    cp.is_legend_locked = False
    cp.save()  # signal deletes the secret
    assert legend_version(race.id) != after_lock


@pytest.mark.django_db
def test_legend_version_build_independent():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Build indep", slug="build-indep-legend")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="cp", is_legend_locked=True
    )
    CheckpointTag.objects.create(checkpoint=cp, nfc_uid="AA:BB", check_method="offline")
    # No key_id argument any more; deterministic regardless of build.
    assert legend_version(race.id) == legend_version(race.id)


@pytest.mark.django_db
def test_legend_version_tagless_race_stable_and_no_crash():
    from apps.mobile.versioning import legend_version
    from website.models.race import Race

    race = Race.objects.create(name="Tagless", slug="tagless-legend")
    # No checkpoints/tags/secrets: empty aggregates render "None"; must not
    # crash and must be deterministic.
    assert legend_version(race.id) == legend_version(race.id)
    assert legend_version(race.id)  # non-empty


# --- mobile TeamSerializer --------------------------------------------------

TEAM_FIELDS = {
    "id",
    "teamname",
    "start_number",
    "category2",
    "ucount",
    "paid_people",
    "start_time",
    "finish_time",
    "members",
}
MEMBER_FIELDS = {"name", "number_in_team"}


@pytest.mark.django_db
def test_team_serializer_output_keys_match_spec(django_user_model):
    from apps.mobile.serializers import TeamSerializer
    from website.models.models import Team

    race, category = _make_race_with_category(slug="ser-keys")
    user = django_user_model.objects.create_user(
        username="ser1", email="ser1@example.com", password="x"
    )
    team = Team.objects.create(
        owner=user, category2=category, teamname="Delta", ucount=2
    )
    data = TeamSerializer(team).data
    assert set(data.keys()) == TEAM_FIELDS
    assert data["category2"] == category.id
    assert data["members"] == []


@pytest.mark.django_db
def test_team_serializer_members_in_number_order(django_user_model):
    from apps.mobile.serializers import TeamSerializer
    from website.models.models import Athlet, Team

    race, category = _make_race_with_category(slug="ser-members")
    user = django_user_model.objects.create_user(
        username="ser2", email="ser2@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Echo")
    Athlet.objects.create(owner=user, team=team, name="Second", number_in_team=2)
    Athlet.objects.create(owner=user, team=team, name="First", number_in_team=1)

    team = Team.objects.prefetch_related(
        Prefetch(
            "athlet_set",
            queryset=Athlet.objects.order_by("number_in_team", "id"),
        )
    ).get(pk=team.pk)
    data = TeamSerializer(team).data
    assert [m["name"] for m in data["members"]] == ["First", "Second"]
    assert set(data["members"][0].keys()) == MEMBER_FIELDS


@pytest.mark.django_db
def test_team_serializer_pads_empty_slots_to_paid_people(django_user_model):
    """A team that paid for 6 but named 3 gets 6 slots, 4..6 empty."""
    from apps.mobile.serializers import TeamSerializer
    from website.models.models import Team

    race, category = _make_race_with_category(slug="ser-pad")
    user = django_user_model.objects.create_user(
        username="ser-pad", email="ser-pad@example.com", password="x"
    )
    team = Team.objects.create(
        owner=user,
        category2=category,
        teamname="Padded",
        ucount=6,
        paid_people=6,
        athlet1="A",
        athlet2="B",
        athlet3="C",
    )
    data = TeamSerializer(team).data
    assert [(m["number_in_team"], m["name"]) for m in data["members"]] == [
        (1, "A"),
        (2, "B"),
        (3, "C"),
        (4, ""),
        (5, ""),
        (6, ""),
    ]


@pytest.mark.django_db
def test_team_serializer_caps_slots_at_ucount(django_user_model):
    """paid_people above ucount is capped by the declared team size."""
    from apps.mobile.serializers import TeamSerializer
    from website.models.models import Team

    race, category = _make_race_with_category(slug="ser-cap")
    user = django_user_model.objects.create_user(
        username="ser-cap", email="ser-cap@example.com", password="x"
    )
    team = Team.objects.create(
        owner=user,
        category2=category,
        teamname="Capped",
        ucount=5,
        paid_people=6,
        athlet1="A",
    )
    data = TeamSerializer(team).data
    assert [m["number_in_team"] for m in data["members"]] == [1, 2, 3, 4, 5]
    assert data["members"][0]["name"] == "A"
    assert all(m["name"] == "" for m in data["members"][1:])


@pytest.mark.django_db
def test_team_serializer_never_drops_named_members(django_user_model):
    """Named members beyond min(paid, ucount) are still emitted (stale data)."""
    from apps.mobile.serializers import TeamSerializer
    from website.models.models import Team

    race, category = _make_race_with_category(slug="ser-keep")
    user = django_user_model.objects.create_user(
        username="ser-keep", email="ser-keep@example.com", password="x"
    )
    team = Team.objects.create(
        owner=user,
        category2=category,
        teamname="Stale",
        ucount=2,
        paid_people=2,
        athlet1="A",
        athlet2="B",
        athlet3="C",
    )
    data = TeamSerializer(team).data
    assert [m["name"] for m in data["members"]] == ["A", "B", "C"]


@pytest.mark.django_db
def test_team_serializer_category2_null_when_unset(django_user_model):
    from apps.mobile.serializers import TeamSerializer
    from website.models.models import Team

    user = django_user_model.objects.create_user(
        username="ser3", email="ser3@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=None, teamname="Foxtrot")
    data = TeamSerializer(team).data
    assert data["category2"] is None


@pytest.mark.django_db
def test_category_serializer_output_keys_match_spec():
    from apps.mobile.serializers import CategorySerializer

    race, category = _make_race_with_category(slug="ser-cat-keys")
    category.short_name = "O"
    category.order = 3
    category.save()
    data = CategorySerializer(category).data
    assert set(data.keys()) == {"id", "code", "short_name", "name", "order"}
    assert data["short_name"] == "O"
    assert data["order"] == 3
    assert "is_active" not in data


# --- TeamsView request-level ------------------------------------------------


@pytest.mark.django_db
def test_teams_valid_signature_returns_200_with_fields_and_members(
    client, settings, django_user_model
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Athlet, Team

    race, category = _make_race_with_category(slug="teams-200")
    user = django_user_model.objects.create_user(
        username="t1", email="t1@example.com", password="x"
    )
    team = Team.objects.create(
        owner=user, category2=category, teamname="Alpha", ucount=2
    )
    Athlet.objects.create(owner=user, team=team, name="Second", number_in_team=2)
    Athlet.objects.create(owner=user, team=team, name="First", number_in_team=1)

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    etag = response["ETag"]
    assert etag.startswith('"') and etag.endswith('"')
    data = response.json()
    assert data["race"] == race.id
    assert len(data["teams"]) == 1
    team_data = data["teams"][0]
    assert set(team_data.keys()) == TEAM_FIELDS
    assert team_data["category2"] == category.id
    assert [m["name"] for m in team_data["members"]] == ["First", "Second"]
    assert set(team_data["members"][0].keys()) == MEMBER_FIELDS


@pytest.mark.django_db
def test_teams_orders_by_id(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race, category = _make_race_with_category(slug="teams-order")
    user = django_user_model.objects.create_user(
        username="t2", email="t2@example.com", password="x"
    )
    first = Team.objects.create(owner=user, category2=category, teamname="Alpha")
    second = Team.objects.create(owner=user, category2=category, teamname="Bravo")

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    ids = [t["id"] for t in response.json()["teams"]]
    assert ids == [first.id, second.id]


@pytest.mark.django_db
def test_teams_if_none_match_returns_304_empty_body(
    client, settings, django_user_model
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race, category = _make_race_with_category(slug="teams-304")
    user = django_user_model.objects.create_user(
        username="t3", email="t3@example.com", password="x"
    )
    Team.objects.create(owner=user, category2=category, teamname="Alpha")

    path = f"/app/race/{race.id}/teams/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    etag = first["ETag"]

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = etag
    second = client.get(path, **headers)

    assert second.status_code == 304
    assert second["ETag"] == etag
    assert second.content == b""
    assert AppInstall.objects.get(install_id="install-abc").request_count == 2


@pytest.mark.django_db
def test_teams_etag_changes_when_athlet_renamed(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Athlet, Team

    race, category = _make_race_with_category(slug="teams-etag")
    user = django_user_model.objects.create_user(
        username="t4", email="t4@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Alpha")
    athlet = Athlet.objects.create(
        owner=user, team=team, name="Runner", number_in_team=1
    )

    path = f"/app/race/{race.id}/teams/"
    before = client.get(path, **_signed_headers("GET", path, SECRET))

    athlet.name = "Runner renamed"
    athlet.save()

    after = client.get(path, **_signed_headers("GET", path, SECRET))
    assert after.status_code == 200
    assert after["ETag"] != before["ETag"]


@pytest.mark.django_db
def test_teams_stale_if_none_match_returns_200_with_new_etag(
    client, settings, django_user_model
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Athlet, Team

    race, category = _make_race_with_category(slug="teams-stale-304")
    user = django_user_model.objects.create_user(
        username="t5", email="t5@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Beta")
    athlet = Athlet.objects.create(
        owner=user, team=team, name="Runner2", number_in_team=1
    )

    path = f"/app/race/{race.id}/teams/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    old_etag = first["ETag"]

    athlet.name = "Runner2 Renamed"
    athlet.save()

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = old_etag
    second = client.get(path, **headers)

    assert second.status_code == 200
    assert second["ETag"] != old_etag


@pytest.mark.django_db
def test_teams_soft_delete_changes_etag(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race, category = _make_race_with_category(slug="teams-del-etag")
    user = django_user_model.objects.create_user(
        username="td2", email="td2@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Phantom")

    path = f"/app/race/{race.id}/teams/"
    before = client.get(path, **_signed_headers("GET", path, SECRET))

    team.is_deleted = True
    team.save()

    after = client.get(path, **_signed_headers("GET", path, SECRET))
    assert after.status_code == 200
    assert after["ETag"] != before["ETag"]
    assert after.json()["teams"] == []


@pytest.mark.django_db
def test_teams_valid_sig_nonexistent_race_returns_404(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = "/app/race/999999/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_teams_unpublished_race_returns_404(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race = Race.objects.create(name="Unpub", slug="unpub-teams", is_published=False)
    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_teams_no_headers_returns_403(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    race, _ = _make_race_with_category(slug="teams-403")
    path = f"/app/race/{race.id}/teams/"
    response = client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_teams_wrong_signature_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="teams-bad-sig")
    path = f"/app/race/{race.id}/teams/"
    headers = _signed_headers("GET", path, "wrong-secret")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_teams_tampered_query_string_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="teams-qs")
    path = f"/app/race/{race.id}/teams/"
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path + "?foo=bar", **headers)
    assert response.status_code == 403


@pytest.mark.django_db
def test_teams_excludes_other_race_teams(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race1, cat1 = _make_race_with_category(slug="teams-iso-r1")
    race2, cat2 = _make_race_with_category(slug="teams-iso-r2")
    user = django_user_model.objects.create_user(
        username="iso1", email="iso1@example.com", password="x"
    )
    team1 = Team.objects.create(owner=user, category2=cat1, teamname="Race1 Team")
    Team.objects.create(owner=user, category2=cat2, teamname="Race2 Team")

    path = f"/app/race/{race1.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    ids = [t["id"] for t in response.json()["teams"]]
    assert ids == [team1.id]


@pytest.mark.django_db
def test_teams_records_appinstall(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="teams-install")
    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200
    install = AppInstall.objects.get(install_id="install-abc")
    assert install.request_count == 1


@pytest.mark.django_db
def test_teams_stats_write_failure_does_not_break_response(
    client, settings, monkeypatch
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="teams-stats-fail")

    def boom(*args, **kwargs):
        raise IntegrityError("simulated stats write failure")

    monkeypatch.setattr(AppInstall.objects, "update_or_create", boom)

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["race"] == race.id
    assert not AppInstall.objects.filter(install_id="install-abc").exists()


@pytest.mark.django_db
def test_teams_excludes_category2_none_team(client, settings, django_user_model):
    """Teams with category2=None are not owned by any race and must be absent."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race, _ = _make_race_with_category(slug="teams-no-cat")
    user = django_user_model.objects.create_user(
        username="nc1", email="nc1@example.com", password="x"
    )
    Team.objects.create(owner=user, category2=None, teamname="Orphan")

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["teams"] == []


@pytest.mark.django_db
def test_teams_empty_race_returns_empty_list(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="teams-empty")
    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200
    data = response.json()
    assert data["race"] == race.id
    assert data["teams"] == []


@pytest.mark.django_db
def test_teams_excludes_soft_deleted_team(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race, category = _make_race_with_category(slug="teams-deleted")
    user = django_user_model.objects.create_user(
        username="td1", email="td1@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Ghost")
    team.is_deleted = True
    team.save()

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["teams"] == []


# --- TeamsView embedded categories ------------------------------------------

CATEGORY_FIELDS = {"id", "code", "short_name", "name", "order"}


@pytest.mark.django_db
def test_teams_categories_present_with_fields_and_order(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.race import Category, Race

    race = Race.objects.create(name="Cat race", slug="cat-race")
    # Insert out of `order` so the response ordering is exercised.
    Category.objects.create(
        code="sport", short_name="S", name="Sport", race=race, order=2
    )
    Category.objects.create(
        code="open", short_name="O", name="Open", race=race, order=1
    )

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert data["race"] == race.id
    cats = data["categories"]
    assert [c["code"] for c in cats] == ["open", "sport"]  # by order, id
    assert set(cats[0].keys()) == CATEGORY_FIELDS
    assert "is_active" not in cats[0]


@pytest.mark.django_db
def test_teams_categories_order_tiebreak_by_id(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.race import Category, Race

    race = Race.objects.create(name="Tie race", slug="tie-race")
    c1 = Category.objects.create(code="a", name="A", race=race, order=0)
    c2 = Category.objects.create(code="b", name="B", race=race, order=0)

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    ids = [c["id"] for c in response.json()["categories"]]
    assert ids == [c1.id, c2.id]  # equal order → id ascending


@pytest.mark.django_db
def test_teams_categories_include_inactive(client, settings, django_user_model):
    """A deactivated category referenced by a team is still listed."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team
    from website.models.race import Category, Race

    race = Race.objects.create(name="Inactive cat", slug="inactive-cat")
    active = Category.objects.create(code="open", name="Open", race=race, order=1)
    dead = Category.objects.create(
        code="legacy", name="Legacy", race=race, order=2, is_active=False
    )
    user = django_user_model.objects.create_user(
        username="ic1", email="ic1@example.com", password="x"
    )
    Team.objects.create(owner=user, category2=dead, teamname="Old team")

    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    cats = response.json()["categories"]
    codes = [c["code"] for c in cats]
    ids = [c["id"] for c in cats]
    assert set(codes) == {"open", "legacy"}
    assert active.id in ids
    assert dead.id in ids


@pytest.mark.django_db
def test_teams_categories_empty_when_no_categories(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.race import Race

    race = Race.objects.create(name="No cats", slug="no-cats-teams")
    path = f"/app/race/{race.id}/teams/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert data["categories"] == []
    assert data["teams"] == []


@pytest.mark.django_db
def test_teams_category_rename_stale_etag_refetches_then_304(client, settings):
    """After a category rename a stale If-None-Match gets 200; the fresh one 304s."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, category = _make_race_with_category(slug="teams-cat-rename")

    path = f"/app/race/{race.id}/teams/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    old_etag = first["ETag"]

    category.name = "Open renamed"
    category.save()

    # Stale ETag → fresh 200 with the new name + a new ETag.
    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = old_etag
    second = client.get(path, **headers)
    assert second.status_code == 200
    new_etag = second["ETag"]
    assert new_etag != old_etag
    assert second.json()["categories"][0]["name"] == "Open renamed"

    # Fresh ETag → 304.
    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = new_etag
    third = client.get(path, **headers)
    assert third.status_code == 304
    assert third.content == b""


@pytest.mark.django_db
def test_teams_sync_versions_teams_moves_after_category_edit(client, settings):
    """versions.teams equals the teams ETag and moves after a category edit."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, category = _make_race_with_category(slug="teams-cat-sync")

    teams_path = f"/app/race/{race.id}/teams/"
    sync_path = f"/app/race/{race.id}/sync/"

    teams_resp = client.get(teams_path, **_signed_headers("GET", teams_path, SECRET))
    sync_resp = client.get(sync_path, **_signed_headers("GET", sync_path, SECRET))
    before_bare = sync_resp.json()["versions"]["teams"]
    assert teams_resp["ETag"] == f'"{before_bare}"'

    category.name = "Open renamed"
    category.save()

    sync_resp2 = client.get(sync_path, **_signed_headers("GET", sync_path, SECRET))
    after_bare = sync_resp2.json()["versions"]["teams"]
    assert after_bare != before_bare

    teams_resp2 = client.get(teams_path, **_signed_headers("GET", teams_path, SECRET))
    assert teams_resp2["ETag"] == f'"{after_bare}"'


# --- SyncView request-level -------------------------------------------------


@pytest.mark.django_db
def test_sync_manifest_shape_and_defaults(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MOBILE_DATA_SOURCE = "cloud"

    from website.models.models import Team

    race, category = _make_race_with_category(slug="sync-shape")
    user = django_user_model.objects.create_user(
        username="s1", email="s1@example.com", password="x"
    )
    Team.objects.create(owner=user, category2=category, teamname="Alpha")

    path = f"/app/race/{race.id}/sync/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"race", "data_source", "lease_expires_at", "versions"}
    assert data["race"] == race.id
    assert data["data_source"] == "cloud"
    assert data["lease_expires_at"] is None
    assert set(data["versions"].keys()) == {"teams", "legend", "member_tags"}
    assert data["versions"]["teams"]  # non-empty fingerprint
    assert data["versions"]["legend"]  # non-empty fingerprint
    assert data["versions"]["member_tags"]  # non-empty fingerprint


@pytest.mark.django_db
def test_sync_versions_teams_matches_teams_etag(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.models import Team

    race, category = _make_race_with_category(slug="sync-etag")
    user = django_user_model.objects.create_user(
        username="s2", email="s2@example.com", password="x"
    )
    Team.objects.create(owner=user, category2=category, teamname="Alpha")

    teams_path = f"/app/race/{race.id}/teams/"
    teams_resp = client.get(teams_path, **_signed_headers("GET", teams_path, SECRET))
    etag = teams_resp["ETag"]

    sync_path = f"/app/race/{race.id}/sync/"
    sync_resp = client.get(sync_path, **_signed_headers("GET", sync_path, SECRET))

    bare = sync_resp.json()["versions"]["teams"]
    assert etag == f'"{bare}"'  # /teams/ ETag is the bare version wrapped in quotes


@pytest.mark.django_db
def test_sync_versions_legend_matches_legend_etag(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Sync legend", slug="sync-legend-etag")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="first")

    legend_path = f"/app/race/{race.id}/legend/"
    legend_resp = client.get(legend_path, **_signed_headers("GET", legend_path, SECRET))
    etag = legend_resp["ETag"]

    sync_path = f"/app/race/{race.id}/sync/"
    sync_resp = client.get(sync_path, **_signed_headers("GET", sync_path, SECRET))

    bare = sync_resp.json()["versions"]["legend"]
    assert etag == f'"{bare}"'  # /legend/ ETag is the bare version wrapped in quotes


@pytest.mark.django_db
def test_sync_versions_legend_matches_legend_etag_per_key_id(client, settings):
    # versions.legend from /sync/ (bare) must equal the legend endpoint ETag
    # (unquoted) for every build — the legend is build-independent, so the
    # cross-endpoint single-source contract holds regardless of key_id.
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"build-a": "secret-a", "build-b": "secret-b"}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Sync per build", slug="sync-legend-per-key")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )

    legend_path = f"/app/race/{race.id}/legend/"
    sync_path = f"/app/race/{race.id}/sync/"

    for key_id, secret in (("build-a", "secret-a"), ("build-b", "secret-b")):
        legend_resp = client.get(
            legend_path, **_signed_headers("GET", legend_path, secret, key_id=key_id)
        )
        sync_resp = client.get(
            sync_path, **_signed_headers("GET", sync_path, secret, key_id=key_id)
        )
        bare = sync_resp.json()["versions"]["legend"]
        assert legend_resp["ETag"] == f'"{bare}"'


@pytest.mark.django_db
def test_sync_versions_legend_same_across_key_ids(client, settings):
    """versions.legend is build-independent: identical across builds."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"build-a": "secret-a", "build-b": "secret-b"}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Sync same build", slug="sync-legend-same-key")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="first", is_legend_locked=True
    )
    CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )

    sync_path = f"/app/race/{race.id}/sync/"
    resp_a = client.get(
        sync_path, **_signed_headers("GET", sync_path, "secret-a", key_id="build-a")
    )
    resp_b = client.get(
        sync_path, **_signed_headers("GET", sync_path, "secret-b", key_id="build-b")
    )

    assert resp_a.json()["versions"]["legend"] == resp_b.json()["versions"]["legend"]


@pytest.mark.django_db
def test_sync_respects_data_source_setting(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MOBILE_DATA_SOURCE = "local"

    race, _ = _make_race_with_category(slug="sync-local")
    path = f"/app/race/{race.id}/sync/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["data_source"] == "local"


@pytest.mark.django_db
def test_sync_valid_sig_nonexistent_race_returns_404(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = "/app/race/999999/sync/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_sync_unpublished_race_returns_404(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race = Race.objects.create(name="Unpub", slug="unpub-sync", is_published=False)
    path = f"/app/race/{race.id}/sync/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_sync_no_headers_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    race, _ = _make_race_with_category(slug="sync-403")
    path = f"/app/race/{race.id}/sync/"
    response = client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_sync_wrong_signature_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="sync-bad-sig")
    path = f"/app/race/{race.id}/sync/"
    headers = _signed_headers("GET", path, "wrong-secret")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_sync_tampered_query_string_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="sync-qs")
    path = f"/app/race/{race.id}/sync/"
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path + "?foo=bar", **headers)
    assert response.status_code == 403


# --- Key rotation (request-level) -------------------------------------------


@pytest.mark.django_db
def test_legend_two_active_keys_both_verify(client, settings, race_with_checkpoints):
    # Rotation overlap: two keys active at once, each app build verifies with its
    # own paired secret + key-id.
    settings.MOBILE_APP_KEYS = {"android-v1": "secret-1", "ios-v1": "secret-2"}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    resp1 = client.get(
        path, **_signed_headers("GET", path, "secret-1", key_id="android-v1")
    )
    assert resp1.status_code == 200

    resp2 = client.get(
        path, **_signed_headers("GET", path, "secret-2", key_id="ios-v1")
    )
    assert resp2.status_code == 200


@pytest.mark.django_db
def test_legend_unknown_key_id_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    headers = _signed_headers("GET", path, SECRET, key_id="not-in-map")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_missing_key_id_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    headers = _signed_headers("GET", path, SECRET, key_id=None)
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_valid_key_id_wrong_secret_returns_403(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    headers = _signed_headers("GET", path, "wrong-secret", key_id="test-v1")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_records_appinstall_key_id(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"android-v1": "secret-1"}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    response = client.get(
        path, **_signed_headers("GET", path, "secret-1", key_id="android-v1")
    )
    assert response.status_code == 200
    install = AppInstall.objects.get(install_id="install-abc")
    assert install.key_id == "android-v1"


@pytest.mark.django_db
def test_appauthfailure_key_id_and_reason_granularity():
    """Rows differing only in key_id or reason persist separately."""
    base = dict(ip="1.2.3.4", reason="bad_sig")
    AppAuthFailure.objects.create(key_id="a-v1", count=1, **base)
    AppAuthFailure.objects.create(key_id="a-v2", count=1, **base)
    AppAuthFailure.objects.create(
        ip="1.2.3.4", key_id="a-v1", reason="unknown_key", count=1
    )
    assert AppAuthFailure.objects.count() == 3


@pytest.mark.django_db
def test_appauthfailure_update_or_create_reuses_row():
    """A second update_or_create with the same (ip, key_id, reason) reuses the row."""
    key = dict(ip="1.2.3.4", key_id="a-v1", reason="bad_sig")
    obj1, created1 = AppAuthFailure.objects.update_or_create(
        defaults={"last_path": "/app/x"}, **key
    )
    assert created1 is True
    obj2, created2 = AppAuthFailure.objects.update_or_create(
        defaults={"last_path": "/app/y"}, **key
    )
    assert created2 is False
    assert obj1.pk == obj2.pk
    assert AppAuthFailure.objects.count() == 1


# --- AppAPIView.permission_denied: log + record 403 (Task 4) ----------------


@pytest.mark.django_db
def test_bad_signature_records_authfailure_and_increments(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    response = client.get(path, **_signed_headers("GET", path, "wrong-secret"))
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}

    row = AppAuthFailure.objects.get(reason="bad_sig")
    assert row.count == 1
    assert row.key_id == "test-v1"
    assert row.last_path == path
    assert row.last_install_id == "install-abc"

    response = client.get(path, **_signed_headers("GET", path, "wrong-secret"))
    assert response.status_code == 403
    row.refresh_from_db()
    assert row.count == 2
    assert AppAuthFailure.objects.filter(reason="bad_sig").count() == 1


@pytest.mark.django_db
def test_unknown_key_records_authfailure(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    response = client.get(
        path, **_signed_headers("GET", path, SECRET, key_id="nope-v9")
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}

    row = AppAuthFailure.objects.get(reason="unknown_key")
    assert row.key_id == ""  # normalised — claimed key is untrusted


@pytest.mark.django_db
def test_unknown_key_different_fake_key_ids_aggregate_to_one_row(
    client, settings, race_with_checkpoints
):
    """Two requests with different fake key_ids must aggregate to a single row.

    Without normalisation the claimed key_id would vary the unique constraint
    and each request would create a new row (row-per-attempt under attack).
    """
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    client.get(path, **_signed_headers("GET", path, SECRET, key_id="fake-key-1"))
    client.get(path, **_signed_headers("GET", path, SECRET, key_id="fake-key-2"))

    assert AppAuthFailure.objects.filter(reason="unknown_key").count() == 1
    row = AppAuthFailure.objects.get(reason="unknown_key")
    assert row.count == 2
    assert row.key_id == ""


@pytest.mark.django_db
def test_missing_headers_records_authfailure_with_blank_key_id(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    response = client.get(path)
    assert response.status_code == 403

    row = AppAuthFailure.objects.get(reason="missing_headers")
    assert row.key_id == ""


@pytest.mark.django_db
def test_missing_headers_different_fake_key_ids_aggregate_to_one_row(
    client, settings, race_with_checkpoints
):
    """Different X-App-Key-Id values with a missing header must aggregate to one row.

    Without normalisation the claimed key_id would vary the unique constraint
    and each request would create a new row (row-per-attempt under attack).
    """
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    ts = str(int(time.time()))

    # Provide key_id but omit X-App-Sig — triggers missing_headers for each request
    client.get(
        path, HTTP_X_APP_KEY_ID="fake-key-1", HTTP_X_APP_TS=ts, HTTP_X_INSTALL_ID="x"
    )
    client.get(
        path, HTTP_X_APP_KEY_ID="fake-key-2", HTTP_X_APP_TS=ts, HTTP_X_INSTALL_ID="x"
    )

    assert AppAuthFailure.objects.filter(reason="missing_headers").count() == 1
    row = AppAuthFailure.objects.get(reason="missing_headers")
    assert row.count == 2
    assert row.key_id == ""


@pytest.mark.django_db
def test_expired_ts_records_authfailure(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    old_ts = str(int(time.time()) - 1000)
    canonical = build_canonical("GET", path, old_ts, b"")
    headers = {
        "HTTP_X_APP_KEY_ID": "test-v1",
        "HTTP_X_APP_SIG": sign(SECRET, canonical),
        "HTTP_X_APP_TS": old_ts,
        "HTTP_X_INSTALL_ID": "install-abc",
    }
    response = client.get(path, **headers)
    assert response.status_code == 403

    assert AppAuthFailure.objects.filter(reason="expired_ts").exists()


@pytest.mark.django_db
def test_valid_request_records_no_authfailure(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200
    assert not AppAuthFailure.objects.exists()


@pytest.mark.django_db
def test_denied_request_records_no_appinstall(client, settings, race_with_checkpoints):
    """A denied request creates an AppAuthFailure but never an AppInstall row."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"

    response = client.get(path, **_signed_headers("GET", path, "wrong-secret"))
    assert response.status_code == 403
    assert AppAuthFailure.objects.count() == 1
    assert not AppInstall.objects.exists()


@pytest.mark.django_db
def test_authfailure_write_failure_does_not_break_403(
    client, settings, race_with_checkpoints, monkeypatch
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    def boom(*args, **kwargs):
        raise IntegrityError("simulated authfailure write failure")

    monkeypatch.setattr(AppAuthFailure.objects, "update_or_create", boom)

    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, "wrong-secret"))

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert not AppAuthFailure.objects.exists()


@pytest.mark.django_db
def test_appauthfailure_admin_changelist_loads(client, django_user_model):
    """The read-only AppAuthFailure changelist loads (200) for a superuser."""
    AppAuthFailure.objects.create(
        ip="1.2.3.4", key_id="android-v1", reason="bad_sig", count=3
    )
    admin = django_user_model.objects.create_superuser(
        username="admin", email="admin@example.com", password="pw"
    )
    client.force_login(admin)

    response = client.get("/admin/mobile/appauthfailure/")
    assert response.status_code == 200


# --- Legend crypto primitives (apps.mobile.crypto) ---------------------------

import os  # noqa: E402

from cryptography.exceptions import InvalidTag  # noqa: E402

from apps.mobile.crypto import derive_wrap_key, seal, unseal  # noqa: E402


def test_seal_unseal_roundtrip():
    key = os.urandom(32)
    enc = seal(key, b"hello world", aad=b"153")
    assert set(enc) == {"iv", "ct"}
    assert unseal(key, enc, aad=b"153") == b"hello world"


def test_unseal_wrong_key_raises():
    enc = seal(os.urandom(32), b"secret", aad=b"153")
    with pytest.raises(InvalidTag):
        unseal(os.urandom(32), enc, aad=b"153")


def test_unseal_wrong_aad_raises():
    key = os.urandom(32)
    enc = seal(key, b"secret", aad=b"153")
    with pytest.raises(InvalidTag):
        unseal(key, enc, aad=b"154")


def test_unseal_tampered_ct_raises():
    import base64

    key = os.urandom(32)
    enc = seal(key, b"secret", aad=b"153")
    raw = bytearray(base64.b64decode(enc["ct"]))
    raw[0] ^= 0x01
    enc["ct"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(InvalidTag):
        unseal(key, enc, aad=b"153")


def test_derive_wrap_key_deterministic_and_distinct():
    code = os.urandom(16)
    assert derive_wrap_key(code) == derive_wrap_key(code)
    assert len(derive_wrap_key(code)) == 32
    assert derive_wrap_key(code) != derive_wrap_key(os.urandom(16))


# --- Task 2: legend-encryption data model ------------------------------------


@pytest.mark.django_db
def test_checkpoint_secret_o2o_reverse():
    """A CheckpointSecret is reachable via the ``checkpoint.secret`` reverse O2O."""
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Secret race", slug="secret-race")
    # Unlocked, so the Task 4 post_save signal does not auto-create a secret —
    # this test owns the single secret it creates to exercise the reverse O2O.
    point = Checkpoint.objects.create(race=race, number=1, cost=4, description="tree")
    secret = CheckpointSecret.objects.create(
        checkpoint=point,
        content_key=os.urandom(32),
        enc_blob={"iv": "aa", "ct": "bb"},
    )

    assert point.secret == secret
    assert len(secret.content_key) == 32
    assert secret.enc_blob == {"iv": "aa", "ct": "bb"}


@pytest.mark.django_db
def test_checkpoint_is_legend_locked_default():
    """``is_legend_locked`` defaults to False."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Lock race", slug="lock-race")
    point = Checkpoint.objects.create(race=race, number=1, cost=1)

    assert point.is_legend_locked is False


@pytest.mark.django_db
def test_checkpoint_tag_unlocks_m2m_add_and_clear():
    """``CheckpointTag.unlocks`` M2M can add and clear, with the reverse accessor."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Unlock race", slug="unlock-race")
    p1 = Checkpoint.objects.create(race=race, number=1, cost=1)
    p2 = Checkpoint.objects.create(race=race, number=2, cost=1)
    tag = CheckpointTag.objects.create(checkpoint=p1, nfc_uid="DEADBEEF")

    tag.unlocks.add(p1, p2)
    assert set(tag.unlocks.values_list("id", flat=True)) == {p1.id, p2.id}
    assert tag in p2.unlocked_by.all()

    tag.unlocks.clear()
    assert tag.unlocks.count() == 0


@pytest.mark.django_db
def test_checkpoint_tag_new_fields_nullable_defaults():
    """``code``/``bundle_blob`` default null; ``bid`` defaults to empty string.

    Checked on an **unsaved** instance: once saved, the Task 4 post_save signal
    populates ``code``/``bid``/``bundle_blob`` (the round-trip persistence of
    those columns is covered by the Task 3/4 ``build_bundle`` tests).
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag defaults", slug="tag-defaults")
    point = Checkpoint.objects.create(race=race, number=1, cost=1)
    tag = CheckpointTag(checkpoint=point, nfc_uid="CAFEBABE")  # unsaved

    assert tag.code is None
    assert tag.bundle_blob is None
    assert tag.bid == ""


# --- Task 3: legend_crypto service layer ------------------------------------


@pytest.mark.django_db
def test_seal_checkpoint_locked_creates_secret_that_decrypts():
    import json

    from apps.mobile.crypto import unseal
    from apps.mobile.legend_crypto import seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Seal locked", slug="seal-locked")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=7, description="столб у воды", is_legend_locked=True
    )

    secret = seal_checkpoint(cp)

    assert CheckpointSecret.objects.filter(checkpoint=cp).exists()
    assert len(bytes(secret.content_key)) == 32
    plaintext = unseal(
        bytes(secret.content_key), secret.enc_blob, aad=str(cp.id).encode()
    )
    assert json.loads(plaintext) == {"cost": 7, "description": "столб у воды"}


@pytest.mark.django_db
def test_seal_checkpoint_unlocked_deletes_existing_secret():
    from apps.mobile.legend_crypto import seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Seal unlock", slug="seal-unlock")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="x", is_legend_locked=True
    )
    seal_checkpoint(cp)
    assert CheckpointSecret.objects.filter(checkpoint=cp).exists()

    cp.is_legend_locked = False
    assert seal_checkpoint(cp) is None
    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()


@pytest.mark.django_db
def test_seal_checkpoint_unlocked_no_secret_is_noop():
    from apps.mobile.legend_crypto import seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Seal open", slug="seal-open")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")

    assert seal_checkpoint(cp) is None
    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()


@pytest.mark.django_db
def test_seal_checkpoint_reseal_keeps_content_key_and_updates_enc():
    import json

    from apps.mobile.crypto import unseal
    from apps.mobile.legend_crypto import seal_checkpoint
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Reseal", slug="reseal")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=3, description="старое", is_legend_locked=True
    )
    first = seal_checkpoint(cp)
    key_before = bytes(first.content_key)
    enc_before = first.enc_blob

    cp.description = "новое"
    second = seal_checkpoint(cp)

    assert bytes(second.content_key) == key_before  # content_key preserved
    assert second.enc_blob != enc_before  # re-sealed (fresh IV + new plaintext)
    plaintext = unseal(key_before, second.enc_blob, aad=str(cp.id).encode())
    assert json.loads(plaintext) == {"cost": 3, "description": "новое"}


@pytest.mark.django_db
def test_ensure_code_generates_only_when_missing():
    from apps.mobile.legend_crypto import ensure_code
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Ensure code", slug="ensure-code")
    point = Checkpoint.objects.create(race=race, number=1, cost=1)
    # Unsaved instance: the Task 4 post_save signal auto-populates code on a
    # saved tag, so test ensure_code's in-memory logic on a fresh instance.
    tag = CheckpointTag(checkpoint=point, nfc_uid="04A1B2C3")

    assert tag.code is None
    ensure_code(tag)
    code1 = bytes(tag.code)
    assert len(code1) == 16

    ensure_code(tag)  # already present → unchanged
    assert bytes(tag.code) == code1


@pytest.mark.django_db
def test_build_bundle_empty_unlocks_falls_back_to_own_locked_point():
    import base64
    import hashlib
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from apps.mobile.legend_crypto import build_bundle, seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Bundle self", slug="bundle-self")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="x", is_legend_locked=True
    )
    secret = seal_checkpoint(cp)
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")

    build_bundle(tag)
    tag.refresh_from_db()

    code = bytes(tag.code)
    assert tag.bid == hashlib.sha256(code).hexdigest()[:16]
    decrypted = json.loads(
        unseal(derive_wrap_key(code), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(cp.id): base64.b64encode(bytes(secret.content_key)).decode()
    }


@pytest.mark.django_db
def test_build_bundle_skips_open_checkpoints():
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from apps.mobile.legend_crypto import build_bundle, seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Bundle skip", slug="bundle-skip")
    locked = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="locked", is_legend_locked=True
    )
    seal_checkpoint(locked)
    open_cp = Checkpoint.objects.create(race=race, number=2, cost=1, description="open")
    tag = CheckpointTag.objects.create(checkpoint=locked, nfc_uid="04A1B2C3")
    tag.unlocks.set([locked, open_cp])

    build_bundle(tag)
    tag.refresh_from_db()

    code = bytes(tag.code)
    decrypted = json.loads(
        unseal(derive_wrap_key(code), tag.bundle_blob, aad=tag.bid.encode())
    )
    # only the locked КП is present; the open one is skipped
    assert set(decrypted.keys()) == {str(locked.id)}


@pytest.mark.django_db
def test_build_bundle_excludes_cross_race_and_hidden_unlocks():
    """Cross-race and hidden КП in tag.unlocks must not contribute content keys.

    A tag in Race A whose unlocks M2M includes a locked КП from Race B or a
    hidden КП must not expose those keys in the bundle.
    """
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from apps.mobile.legend_crypto import build_bundle, seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race_a = Race.objects.create(name="Race A", slug="race-a-xrace")
    race_b = Race.objects.create(name="Race B", slug="race-b-xrace")

    local_locked = Checkpoint.objects.create(
        race=race_a, number=1, cost=5, description="local", is_legend_locked=True
    )
    seal_checkpoint(local_locked)

    cross_race_locked = Checkpoint.objects.create(
        race=race_b, number=1, cost=9, description="cross", is_legend_locked=True
    )
    seal_checkpoint(cross_race_locked)

    hidden_locked = Checkpoint.objects.create(
        race=race_a,
        number=2,
        cost=3,
        description="hidden",
        type="hidden",
        is_legend_locked=True,
    )
    seal_checkpoint(hidden_locked)

    tag = CheckpointTag.objects.create(checkpoint=local_locked, nfc_uid="04AABBCC")
    tag.unlocks.set([local_locked, cross_race_locked, hidden_locked])

    build_bundle(tag)
    tag.refresh_from_db()

    code = bytes(tag.code)
    decrypted = json.loads(
        unseal(derive_wrap_key(code), tag.bundle_blob, aad=tag.bid.encode())
    )
    # Only the same-race non-hidden КП should appear
    assert set(decrypted.keys()) == {str(local_locked.id)}
    assert str(cross_race_locked.id) not in decrypted
    assert str(hidden_locked.id) not in decrypted


@pytest.mark.django_db
def test_build_bundle_invalid_only_unlocks_produces_none_not_fallback():
    """A tag whose explicit unlocks contain *only* cross-race/hidden КП must get
    bundle_blob=None, not silently fall back to [tag.checkpoint].

    Regression for: build_bundle checked ``if not unlocked`` (post-filter) instead
    of ``if not tag.unlocks.exists()`` (raw M2M), so all-invalid explicit unlocks
    triggered the [point] default and granted a key outside the configured subset.
    """
    from apps.mobile.legend_crypto import build_bundle, seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race_a = Race.objects.create(name="Race A", slug="race-a-invalid-only")
    race_b = Race.objects.create(name="Race B", slug="race-b-invalid-only")

    local_cp = Checkpoint.objects.create(
        race=race_a, number=1, cost=5, description="local", is_legend_locked=True
    )
    seal_checkpoint(local_cp)

    cross_race_cp = Checkpoint.objects.create(
        race=race_b, number=1, cost=9, description="cross", is_legend_locked=True
    )
    seal_checkpoint(cross_race_cp)

    hidden_cp = Checkpoint.objects.create(
        race=race_a,
        number=2,
        cost=3,
        description="hidden",
        type="hidden",
        is_legend_locked=True,
    )
    seal_checkpoint(hidden_cp)

    tag = CheckpointTag.objects.create(checkpoint=local_cp, nfc_uid="04DEADBEEF")
    # Both explicit unlocks are invalid — cross-race and hidden only
    tag.unlocks.set([cross_race_cp, hidden_cp])

    build_bundle(tag)
    tag.refresh_from_db()

    # Must not fall back to [local_cp]; no valid keys → bundle_blob=None
    assert tag.bundle_blob is None


@pytest.mark.django_db
def test_build_bundle_overlap_one_checkpoint_in_two_bundles():
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from apps.mobile.legend_crypto import build_bundle, seal_checkpoint
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Bundle overlap", slug="bundle-overlap")
    shared = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="shared", is_legend_locked=True
    )
    extra = Checkpoint.objects.create(
        race=race, number=2, cost=1, description="extra", is_legend_locked=True
    )
    shared_secret = seal_checkpoint(shared)
    seal_checkpoint(extra)

    p_a = Checkpoint.objects.create(race=race, number=3, cost=1)
    p_b = Checkpoint.objects.create(race=race, number=4, cost=1)
    tag_a = CheckpointTag.objects.create(checkpoint=p_a, nfc_uid="0A0A0A0A")
    tag_b = CheckpointTag.objects.create(checkpoint=p_b, nfc_uid="0B0B0B0B")
    tag_a.unlocks.set([shared])
    tag_b.unlocks.set([shared, extra])

    build_bundle(tag_a)
    build_bundle(tag_b)
    tag_a.refresh_from_db()
    tag_b.refresh_from_db()

    shared_b64 = base64.b64encode(bytes(shared_secret.content_key)).decode()
    decoded_a = json.loads(
        unseal(
            derive_wrap_key(bytes(tag_a.code)),
            tag_a.bundle_blob,
            aad=tag_a.bid.encode(),
        )
    )
    decoded_b = json.loads(
        unseal(
            derive_wrap_key(bytes(tag_b.code)),
            tag_b.bundle_blob,
            aad=tag_b.bid.encode(),
        )
    )
    # the shared КП's content_key appears in both bundles (overlap)
    assert decoded_a[str(shared.id)] == shared_b64
    assert decoded_b[str(shared.id)] == shared_b64
    assert set(decoded_b.keys()) == {str(shared.id), str(extra.id)}


@pytest.mark.django_db
def test_build_bundle_preserves_existing_code():
    from apps.mobile.legend_crypto import build_bundle, ensure_code
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Bundle keep code", slug="bundle-keep-code")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="x", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")
    ensure_code(tag)
    tag.save()
    code_before = bytes(tag.code)

    build_bundle(tag)
    tag.refresh_from_db()
    assert bytes(tag.code) == code_before


# --- Task 4: signals --------------------------------------------------------


@pytest.mark.django_db
def test_signal_locking_checkpoint_via_save_creates_secret():
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Sig lock", slug="sig-lock")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")
    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()

    cp.is_legend_locked = True
    cp.save()

    assert CheckpointSecret.objects.filter(checkpoint=cp).exists()


@pytest.mark.django_db
def test_signal_unlocking_checkpoint_via_save_deletes_secret():
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Sig unlock", slug="sig-unlock")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="x", is_legend_locked=True
    )
    assert CheckpointSecret.objects.filter(checkpoint=cp).exists()

    cp.is_legend_locked = False
    cp.save()

    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()


@pytest.mark.django_db
def test_signal_lock_toggle_rebuilds_implicit_point_tag_bundle():
    """A tag with empty unlocks (implicit [point]) is rebuilt on a lock toggle.

    Such a tag is reachable via ``cp.tags`` but **not** ``cp.unlocked_by`` (its
    M2M is empty), so the ``∪ cp.tags`` half of the rebuild set is what catches
    it. Before locking, the tag's bundle is empty (open КП → no content_key);
    after locking it must carry the КП's content_key.
    """
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig implicit", slug="sig-implicit")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3"
    )  # empty unlocks

    tag.refresh_from_db()
    assert tag.bundle_blob is None  # open КП → no content_key, no bundle

    cp.is_legend_locked = True
    cp.save()

    tag.refresh_from_db()
    secret = cp.secret
    after = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert after == {str(cp.id): base64.b64encode(bytes(secret.content_key)).decode()}


@pytest.mark.django_db
def test_signal_lock_toggle_rebuilds_unlocked_by_tag_bundle():
    """A tag on a different point unlocking the КП is rebuilt via cp.unlocked_by."""
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig unlocked-by", slug="sig-unlocked-by")
    target = Checkpoint.objects.create(race=race, number=1, cost=1, description="t")
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="0B0B0B0B")
    tag.unlocks.set([target])  # holder tag unlocks the target КП

    target.is_legend_locked = True
    target.save()

    tag.refresh_from_db()
    secret = target.secret
    decrypted = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(target.id): base64.b64encode(bytes(secret.content_key)).decode()
    }


@pytest.mark.django_db
def test_signal_editing_unlocks_rebuilds_bundle():
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig m2m", slug="sig-m2m")
    locked = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="locked", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="0C0C0C0C")

    tag.unlocks.add(locked)  # m2m_changed → build_bundle

    tag.refresh_from_db()
    decrypted = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(locked.id): base64.b64encode(bytes(locked.secret.content_key)).decode()
    }

    tag.unlocks.clear()  # m2m_changed post_clear → rebuild → no locked КП → None
    tag.refresh_from_db()
    assert tag.bundle_blob is None


@pytest.mark.django_db
def test_signal_reverse_m2m_add_rebuilds_bundle():
    """checkpoint.unlocked_by.add(tag) (reverse relation) triggers a bundle rebuild."""
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig rev-m2m", slug="sig-rev-m2m")
    locked = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="locked", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="0D0D0D0D")

    # Via reverse accessor: fires m2m_changed with instance=locked, pk_set={tag.pk}
    locked.unlocked_by.add(tag)

    tag.refresh_from_db()
    decrypted = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(locked.id): base64.b64encode(bytes(locked.secret.content_key)).decode()
    }


@pytest.mark.django_db
def test_signal_reverse_m2m_clear_rebuilds_bundle():
    """checkpoint.unlocked_by.clear() (reverse relation) rebuilds affected tag bundles.

    pre_clear captures the tag PKs before Django deletes the junction rows so
    that post_clear can still find and rebuild those bundles.
    """

    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig rev-clear", slug="sig-rev-clear")
    locked = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="locked", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="0E0E0E0E")

    locked.unlocked_by.add(tag)
    tag.refresh_from_db()
    assert tag.bundle_blob is not None  # bundle carries the content_key

    # Reverse clear: Django fires pre_clear then post_clear with pk_set=None.
    # The signal must rebuild bundles using PKs captured in pre_clear.
    locked.unlocked_by.clear()
    tag.refresh_from_db()
    # holder is an open checkpoint; no locked КП remain in the unlock set.
    assert tag.bundle_blob is None


@pytest.mark.django_db
def test_signal_content_edit_on_locked_cp_reseals_enc_blob():
    """Editing a locked CP's description re-seals enc_blob with the new plaintext."""
    import json

    from apps.mobile.crypto import unseal
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Sig reseal", slug="sig-reseal")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=5, description="original", is_legend_locked=True
    )
    secret = CheckpointSecret.objects.get(checkpoint=cp)
    key = bytes(secret.content_key)

    cp.description = "updated"
    cp.save()

    secret.refresh_from_db()
    plaintext = json.loads(unseal(key, secret.enc_blob, aad=str(cp.id).encode()))
    assert plaintext["description"] == "updated"
    assert plaintext["cost"] == 5


@pytest.mark.django_db
def test_signal_reverse_m2m_remove_rebuilds_bundle():
    """checkpoint.unlocked_by.remove(tag) rebuilds the tag's bundle (key removed)."""

    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig rev-remove", slug="sig-rev-remove")
    locked = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="locked", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="0F0F0F0F")

    locked.unlocked_by.add(tag)
    tag.refresh_from_db()
    assert tag.bundle_blob is not None  # bundle carries the content_key

    # Reverse remove: fires m2m_changed with action=post_remove, pk_set={tag.pk},
    # instance=locked. The signal must rebuild the tag's bundle.
    locked.unlocked_by.remove(tag)
    tag.refresh_from_db()
    # No locked КП remain in the unlock set → bundle is None.
    assert tag.bundle_blob is None


@pytest.mark.django_db
def test_signal_lock_toggle_rebuilds_all_dependent_tag_bundles():
    """Locking a КП rebuilds bundles for ALL tags that have it in their unlocks.

    Guards the multi-tag loop in checkpoint_saved: if the re-entrancy guard
    accidentally blocked subsequent iterations, only the first tag would be
    rebuilt and the rest would be left with a stale (keyless) bundle.
    """
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig multi-tag", slug="sig-multi-tag")
    target = Checkpoint.objects.create(
        race=race, number=1, cost=5, description="sec", is_legend_locked=False
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag_a = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="A1A1A1A1")
    tag_b = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="B2B2B2B2")

    # Both tags explicitly unlock target (currently open → no content_key yet).
    target.unlocked_by.add(tag_a)
    target.unlocked_by.add(tag_b)
    tag_a.refresh_from_db()
    tag_b.refresh_from_db()
    assert tag_a.bundle_blob is None  # open КП → no key
    assert tag_b.bundle_blob is None

    # Lock the КП: signal must rebuild bundles for BOTH tags.
    target.is_legend_locked = True
    target.save()

    tag_a.refresh_from_db()
    tag_b.refresh_from_db()
    assert tag_a.bundle_blob is not None, "tag_a bundle not rebuilt after lock"
    assert tag_b.bundle_blob is not None, "tag_b bundle not rebuilt after lock"

    # Both bundles must decrypt to the same content_key for target.
    secret = target.secret
    for tag in (tag_a, tag_b):
        decrypted = json.loads(
            unseal(
                derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode()
            )
        )
        assert decrypted == {
            str(target.id): base64.b64encode(bytes(secret.content_key)).decode()
        }


@pytest.mark.django_db
def test_legend_version_moves_on_locked_cp_description_edit():
    """Editing a locked КП's description re-seals, bumping CheckpointSecret.updated_at.

    Guards the ``update_fields`` discipline: if ``seal_checkpoint`` dropped
    ``"updated_at"`` from its ``save(update_fields=…)`` the fingerprint would
    silently stale after a description edit on a locked КП.
    """
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Reseal version", slug="reseal-version")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=5, description="original", is_legend_locked=True
    )
    before = legend_version(race.id)

    cp.description = "updated"
    cp.save()  # signal re-seals → CheckpointSecret.updated_at bumped

    assert legend_version(race.id) != before


@pytest.mark.django_db
def test_signal_no_infinite_recursion():
    """A tag save + m2m change complete without recursing (sentinel + guard)."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig recursion", slug="sig-recursion")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="x", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")

    # Each of these would blow the stack if the receivers re-triggered each other.
    tag.check_method = "online"
    tag.save()
    tag.unlocks.set([cp])
    tag.unlocks.clear()

    tag.refresh_from_db()
    assert tag.bid  # a bundle was built, no RecursionError raised


@pytest.mark.django_db
def test_signal_bundle_rebuild_moves_legend_etag():
    """A bundle rebuild bumps CheckpointTag.updated_at → legend version moves.

    Guards the ``update_fields`` must-include-``updated_at`` rule: if
    ``build_bundle`` dropped ``"updated_at"`` from its update_fields the
    fingerprint would silently stale and this assertion would fail.
    """
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Sig etag", slug="sig-etag")
    first = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="a", is_legend_locked=True
    )
    second = Checkpoint.objects.create(
        race=race, number=2, cost=1, description="b", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=3, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="04A1B2C3")
    tag.unlocks.set([first])

    before = legend_version(race.id)

    tag.unlocks.add(second)  # rebuild folds in `second`'s key + bumps updated_at

    assert legend_version(race.id) != before


@pytest.mark.django_db
def test_signal_hidden_to_kp_on_locked_cp_rebuilds_dependent_bundle():
    """Promoting a locked hidden КП to kp rebuilds bundles of tags that unlock it.

    A tag's explicit unlocks M2M filtered by build_bundle excludes hidden КП, so
    when the CP was hidden the bundle had no content_key for it. After the type
    change the legend serves that CP's enc_blob; without a signal-driven rebuild
    the bundle would remain stale and the app could not decrypt.
    """
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Hidden promote", slug="hidden-promote")
    locked_hidden = Checkpoint.objects.create(
        race=race,
        number=1,
        cost=5,
        description="secret",
        type="hidden",
        is_legend_locked=True,
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="DDDDDDDD")
    tag.unlocks.add(locked_hidden)  # hidden at add time → bundle excludes its key

    tag.refresh_from_db()
    assert tag.bundle_blob is None  # hidden КП excluded from bundle

    # Promote to kp — should trigger a bundle rebuild via pre_save/post_save
    locked_hidden.type = "kp"
    locked_hidden.save()

    tag.refresh_from_db()
    assert tag.bundle_blob is not None  # rebuild happened

    secret = locked_hidden.secret
    decrypted = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(locked_hidden.id): base64.b64encode(bytes(secret.content_key)).decode()
    }


# --- Task 5: management commands --------------------------------------------


@pytest.mark.django_db
def test_rebuild_legend_crypto_backfills_secrets_and_bundles():
    """The backfill re-seals locked КП and repopulates tag bundles.

    Simulates a pre-backfill state (no secret, blank bundle) with bypass-signal
    ``QuerySet.update`` / ``delete``, then asserts the command rebuilds both.
    """
    import json

    from django.core.management import call_command

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointSecret, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Backfill", slug="backfill")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="tree", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")

    # Wipe to a pre-backfill state, bypassing signals.
    CheckpointSecret.objects.filter(checkpoint=cp).delete()
    CheckpointTag.objects.filter(pk=tag.pk).update(bundle_blob=None, bid="")

    call_command("rebuild_legend_crypto", race=race.id)

    secret = CheckpointSecret.objects.get(checkpoint=cp)
    enc = json.loads(
        unseal(bytes(secret.content_key), secret.enc_blob, aad=str(cp.id).encode())
    )
    assert enc == {"cost": 4, "description": "tree"}

    tag.refresh_from_db()
    assert tag.bid
    assert tag.bundle_blob is not None
    bundle = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert str(cp.id) in bundle


@pytest.mark.django_db
def test_rebuild_regenerate_codes_changes_codes_else_preserved():
    from django.core.management import call_command

    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Regen", slug="regen")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="x", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")
    tag.refresh_from_db()
    code_before = bytes(tag.code)

    # Without the flag the existing code is preserved.
    call_command("rebuild_legend_crypto", race=race.id)
    tag.refresh_from_db()
    assert bytes(tag.code) == code_before

    # With the flag a fresh code is minted.
    call_command("rebuild_legend_crypto", race=race.id, regenerate_codes=True)
    tag.refresh_from_db()
    assert bytes(tag.code) != code_before


@pytest.mark.django_db
def test_export_legend_codes_lists_every_tag_code():
    from io import StringIO

    from django.core.management import call_command

    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Export", slug="export")
    cp1 = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="a", is_legend_locked=True
    )
    cp2 = Checkpoint.objects.create(
        race=race, number=2, cost=1, description="b", is_legend_locked=True
    )
    tag1 = CheckpointTag.objects.create(checkpoint=cp1, nfc_uid="0A0A0A0A")
    tag2 = CheckpointTag.objects.create(checkpoint=cp2, nfc_uid="0B0B0B0B")
    tag1.refresh_from_db()
    tag2.refresh_from_db()

    out = StringIO()
    call_command("export_legend_codes", race=race.id, stdout=out)
    output = out.getvalue()

    assert tag1.nfc_uid in output
    assert bytes(tag1.code).hex() in output
    assert tag2.nfc_uid in output
    assert bytes(tag2.code).hex() in output


@pytest.mark.django_db
def test_rebuild_legend_crypto_no_race_backfills_all_races():
    """rebuild_legend_crypto without --race processes all races."""
    from django.core.management import call_command

    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race1 = Race.objects.create(name="All1", slug="all1")
    race2 = Race.objects.create(name="All2", slug="all2")
    cp1 = Checkpoint.objects.create(
        race=race1, number=1, cost=1, description="a", is_legend_locked=True
    )
    cp2 = Checkpoint.objects.create(
        race=race2, number=1, cost=2, description="b", is_legend_locked=True
    )
    # Wipe secrets to simulate pre-backfill state.
    CheckpointSecret.objects.filter(checkpoint__in=[cp1, cp2]).delete()

    call_command("rebuild_legend_crypto")

    assert CheckpointSecret.objects.filter(checkpoint=cp1).exists()
    assert CheckpointSecret.objects.filter(checkpoint=cp2).exists()


@pytest.mark.django_db
def test_export_legend_codes_placeholder_for_missing_code():
    """A tag with no code yet prints '—' instead of a hex value."""
    from io import StringIO

    from django.core.management import call_command

    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Export2", slug="export2")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="0C0C0C0C")
    # Bypass signals to leave code as None.
    CheckpointTag.objects.filter(pk=tag.pk).update(code=None)

    out = StringIO()
    call_command("export_legend_codes", race=race.id, stdout=out)
    output = out.getvalue()

    assert "0C0C0C0C" in output
    assert "\t—" in output


# --- Task 7: admin actions ---------------------------------------------------


@pytest.mark.django_db
def test_admin_lock_legend_action_creates_secret(client, django_user_model):
    """The «Запереть легенду» bulk action seals (not just flips the flag)."""
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Adm lock", slug="adm-lock")
    cp = Checkpoint.objects.create(race=race, number=1, cost=4, description="tree")
    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()

    admin = django_user_model.objects.create_superuser(
        username="a1", email="a1@example.com", password="pw"
    )
    client.force_login(admin)

    response = client.post(
        "/admin/website/checkpoint/",
        {"action": "lock_legend", "_selected_action": [cp.id]},
    )
    assert response.status_code == 302

    cp.refresh_from_db()
    assert cp.is_legend_locked
    secret = CheckpointSecret.objects.get(checkpoint=cp)
    assert secret.enc_blob.get("ct")  # actually sealed, not an empty blob


@pytest.mark.django_db
def test_admin_unlock_legend_action_deletes_secret(client, django_user_model):
    from website.models.checkpoint import Checkpoint, CheckpointSecret
    from website.models.race import Race

    race = Race.objects.create(name="Adm unlock", slug="adm-unlock")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="tree", is_legend_locked=True
    )
    assert CheckpointSecret.objects.filter(checkpoint=cp).exists()

    admin = django_user_model.objects.create_superuser(
        username="a2", email="a2@example.com", password="pw"
    )
    client.force_login(admin)

    response = client.post(
        "/admin/website/checkpoint/",
        {"action": "unlock_legend", "_selected_action": [cp.id]},
    )
    assert response.status_code == 302

    cp.refresh_from_db()
    assert not cp.is_legend_locked
    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()


@pytest.mark.django_db
def test_admin_rebuild_bundle_action_repopulates_blob(client, django_user_model):
    """The «Пересобрать бандл» action rebuilds a blank bundle."""
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Adm rebuild", slug="adm-rebuild")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="tree", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")
    # Wipe the bundle without firing signals (simulate a stale row).
    CheckpointTag.objects.filter(pk=tag.pk).update(bundle_blob=None)
    tag.refresh_from_db()
    assert tag.bundle_blob is None

    admin = django_user_model.objects.create_superuser(
        username="a3", email="a3@example.com", password="pw"
    )
    client.force_login(admin)

    response = client.post(
        "/admin/website/checkpointtag/",
        {"action": "rebuild_bundle", "_selected_action": [tag.id]},
    )
    assert response.status_code == 302

    tag.refresh_from_db()
    assert tag.bundle_blob is not None
    decrypted = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(cp.id): base64.b64encode(bytes(cp.secret.content_key)).decode()
    }


@pytest.mark.django_db
def test_admin_regenerate_code_action_changes_code(client, django_user_model):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Adm regen", slug="adm-regen")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="tree", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="04A1B2C3")
    tag.refresh_from_db()
    code_before = bytes(tag.code)

    admin = django_user_model.objects.create_superuser(
        username="a4", email="a4@example.com", password="pw"
    )
    client.force_login(admin)

    response = client.post(
        "/admin/website/checkpointtag/",
        {"action": "regenerate_code", "_selected_action": [tag.id]},
    )
    assert response.status_code == 302

    tag.refresh_from_db()
    assert bytes(tag.code) != code_before


@pytest.mark.django_db
def test_tag_serializer_open_tag_identity_only():
    """Open-КП tag (no bundle_blob) → {bid, checkpoint_id, check_method}, iv/ct None."""
    from apps.mobile.serializers import TagSerializer
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Open tag ser", slug="open-tag-ser")
    cp = Checkpoint.objects.create(race=race, number=1, cost=2, description="open")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )
    tag.refresh_from_db()
    assert tag.bundle_blob is None  # open КП → no unlock envelope

    data = TagSerializer(tag).data
    assert data["bid"] == tag.bid
    assert data["checkpoint_id"] == cp.id
    assert data["check_method"] == "offline"
    assert data["iv"] is None
    assert data["ct"] is None


@pytest.mark.django_db
def test_tag_serializer_locked_tag_includes_iv_ct():
    """A locked-КП tag → identity fields plus non-null iv/ct from bundle_blob."""
    from apps.mobile.serializers import TagSerializer
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Locked tag ser", slug="locked-tag-ser")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="locked", is_legend_locked=True
    )
    # Empty unlocks falls back to [point]; signals seal + build the bundle.
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", check_method="offline"
    )
    tag.refresh_from_db()
    assert tag.bundle_blob is not None  # locked КП → unlock envelope present

    data = TagSerializer(tag).data
    assert data["bid"] == tag.bid
    assert data["checkpoint_id"] == cp.id
    assert data["check_method"] == "offline"
    assert data["iv"] == tag.bundle_blob["iv"]
    assert data["ct"] == tag.bundle_blob["ct"]
    assert data["iv"] is not None
    assert data["ct"] is not None


@pytest.mark.django_db
def test_export_legend_codes_dumps_open_and_locked_tags():
    """The command exports every tag of a race — open КП included, not just locked."""
    from io import StringIO

    from django.core.management import call_command

    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Export codes", slug="export-codes")
    open_cp = Checkpoint.objects.create(
        race=race, number=1, cost=2, description="open", is_legend_locked=False
    )
    locked_cp = Checkpoint.objects.create(
        race=race, number=2, cost=4, description="locked", is_legend_locked=True
    )
    # Signals mint a code for every tag (open КП too — open just gets bundle_blob=None).
    open_tag = CheckpointTag.objects.create(checkpoint=open_cp, nfc_uid="0A0A0A0A")
    locked_tag = CheckpointTag.objects.create(checkpoint=locked_cp, nfc_uid="0B0B0B0B")
    open_tag.refresh_from_db()
    locked_tag.refresh_from_db()
    assert open_tag.bundle_blob is None  # open КП → no unlock envelope
    assert locked_tag.bundle_blob is not None

    out = StringIO()
    call_command("export_legend_codes", "--race", str(race.id), stdout=out)
    output = out.getvalue()

    # Both nfc_uids and both code hexes appear (open tag is not filtered out).
    assert "0A0A0A0A" in output
    assert "0B0B0B0B" in output
    assert bytes(open_tag.code).hex() in output
    assert bytes(locked_tag.code).hex() in output
    # The placeholder dash is only for code-less tags; both tags have codes here.
    assert "\t—" not in output


@pytest.mark.django_db
def test_signal_bulk_delete_checkpoints_rebuilds_all_dependent_tags():
    """Deleting two locked КП at once rebuilds bundles for *each* КП's dependent tags.

    Regression for the _pre_delete_tags overwrite bug: QuerySet.delete() fires all
    pre_delete signals before any row is deleted, so a naïve .value = [...] approach
    overwrites the first КП's captured tags with the second's, leaving the first КП's
    tags with stale bundle_blobs that still reference the deleted content_key.
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Bulk del", slug="bulk-del")
    # Two locked КП, each with its own distinct cross-КП tag.
    cp_a = Checkpoint.objects.create(
        race=race, number=1, cost=10, description="A", is_legend_locked=True
    )
    cp_b = Checkpoint.objects.create(
        race=race, number=2, cost=20, description="B", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=3, cost=1, description="H")
    tag_a = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="AA000001")
    tag_b = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="BB000002")
    tag_a.unlocks.set([cp_a])  # tag_a unlocks only cp_a
    tag_b.unlocks.set([cp_b])  # tag_b unlocks only cp_b

    tag_a.refresh_from_db()
    tag_b.refresh_from_db()
    assert tag_a.bundle_blob is not None, "tag_a bundle must exist before delete"
    assert tag_b.bundle_blob is not None, "tag_b bundle must exist before delete"

    # Bulk delete: all pre_delete signals fire before any deletion.
    Checkpoint.objects.filter(pk__in=[cp_a.pk, cp_b.pk]).delete()

    tag_a.refresh_from_db()
    tag_b.refresh_from_db()
    # Both tags should have their bundles cleared (no locked КП remaining to unlock).
    assert tag_a.bundle_blob is None, "tag_a bundle should be None after cp_a deleted"
    assert tag_b.bundle_blob is None, "tag_b bundle should be None after cp_b deleted"


@pytest.mark.django_db
def test_signal_bulk_delete_holder_and_target_skips_cascade_deleted_tag():
    """Bulk-deleting both the tag's holder KP and the locked target KP must not crash.

    Repro: tag.checkpoint=holder, tag.unlocks=[target], delete [holder, target]
    together.
    Django cascade-deletes the CheckpointTag row when holder is deleted. The
    post_delete for target must not try to call build_bundle on the now-deleted tag
    (which would attempt tag.save() on a non-existent row).
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Bulk del holder", slug="bulk-del-holder")
    target = Checkpoint.objects.create(
        race=race, number=1, cost=10, description="T", is_legend_locked=True
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="H")
    tag = CheckpointTag.objects.create(checkpoint=holder, nfc_uid="CC000003")
    tag.unlocks.set([target])
    tag_pk = tag.pk

    # Must not raise DatabaseError or IntegrityError.
    Checkpoint.objects.filter(pk__in=[holder.pk, target.pk]).delete()

    # The tag was cascade-deleted along with holder.
    assert not CheckpointTag.objects.filter(pk=tag_pk).exists()


# --- member_tags_version fingerprint ----------------------------------------


@pytest.mark.django_db
def test_member_tags_version_stable_for_unchanged_pool():
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    now = timezone.now()
    Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=now)
    Tag.objects.create(number=2, nfc_uid="AA02", last_seen_at=now - timedelta(days=5))

    assert member_tags_version() == member_tags_version()


@pytest.mark.django_db
def test_member_tags_version_stable_for_empty_pool():
    from apps.mobile.versioning import member_tags_version

    # Empty pool uses "empty" sentinel — stable, non-crashing.
    assert member_tags_version() == member_tags_version()


@pytest.mark.django_db
def test_member_tags_version_changes_on_provisioning_renumber():
    from django.utils import timezone

    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    tag = Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=timezone.now())
    before = member_tags_version()

    tag.number = 99
    tag.save()
    assert member_tags_version() != before


@pytest.mark.django_db
def test_member_tags_version_unchanged_by_touch_within_window():
    """A scan that does not change window membership must not move the version."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    now = timezone.now()
    # Two tags well inside the 30-day window; a touch on one keeps both in-window.
    Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=now - timedelta(days=1))
    tag = Tag.objects.create(
        number=2, nfc_uid="AA02", last_seen_at=now - timedelta(days=2)
    )
    before = member_tags_version()

    # Mirror MemberTagTouchView: bump only last_seen_at, not updated_at.
    tag.last_seen_at = timezone.now()
    tag.save(update_fields=["last_seen_at"])
    assert member_tags_version() == before


@pytest.mark.django_db
def test_active_member_tags_window_anchored_on_max_last_seen():
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.versioning import active_member_tags
    from website.models.tag import Tag

    now = timezone.now()
    fresh = Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=now)
    # Just inside the 30-day floor.
    inside = Tag.objects.create(
        number=2, nfc_uid="AA02", last_seen_at=now - timedelta(days=29)
    )
    # Just outside the 30-day floor.
    Tag.objects.create(number=3, nfc_uid="AA03", last_seen_at=now - timedelta(days=31))

    ids = set(active_member_tags().values_list("id", flat=True))
    assert ids == {fresh.id, inside.id}


@pytest.mark.django_db
def test_active_member_tags_never_scanned_returns_all():
    from apps.mobile.versioning import active_member_tags
    from website.models.tag import Tag

    a = Tag.objects.create(number=1, nfc_uid="AA01")
    b = Tag.objects.create(number=2, nfc_uid="AA02")

    ids = set(active_member_tags().values_list("id", flat=True))
    assert ids == {a.id, b.id}


@pytest.mark.django_db
def test_active_member_tags_never_scanned_included_alongside_recent():
    """A never-scanned tag must remain visible once other tags start being scanned."""
    from django.utils import timezone

    from apps.mobile.versioning import active_member_tags
    from website.models.tag import Tag

    recent = Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=timezone.now())
    never_scanned = Tag.objects.create(number=2, nfc_uid="AA02")

    ids = set(active_member_tags().values_list("id", flat=True))
    assert ids == {recent.id, never_scanned.id}


@pytest.mark.django_db
def test_member_tags_version_changes_on_provisioning_add():
    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    before = member_tags_version()
    Tag.objects.create(number=1, nfc_uid="AA01")
    assert member_tags_version() != before


@pytest.mark.django_db
def test_member_tags_version_changes_on_provisioning_delete():
    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    tag = Tag.objects.create(number=1, nfc_uid="AA01")
    before = member_tags_version()
    tag.delete()
    assert member_tags_version() != before


@pytest.mark.django_db
def test_member_tags_version_changes_on_equal_count_swap():
    """Touch-driven window swap: COUNT and MAX(updated_at) stay equal but
    identities change — must still produce a new fingerprint."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    now = timezone.now()
    # Tag A: the most-recently scanned tag (anchors the window floor).
    # Keep its auto_now updated_at as the MAX — we backdate B and C below.
    tag_a = Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=now)
    # Tag B: near the edge of the 30-day window; will be evicted after the swap.
    tag_b = Tag.objects.create(
        number=2, nfc_uid="AA02", last_seen_at=now - timedelta(days=29)
    )
    # Tag C: currently outside the window.
    tag_c = Tag.objects.create(
        number=3, nfc_uid="AA03", last_seen_at=now - timedelta(days=31)
    )
    # Backdate BOTH B and C so tag_a holds MAX(updated_at) in every state.
    # Without this, B.updated_at > A.updated_at (created later), meaning the old
    # MAX|COUNT fingerprint would also change when B leaves (MAX shifts from B to A)
    # — not a true same-MAX|COUNT swap test.
    Tag.objects.filter(pk=tag_b.pk).update(updated_at=now - timedelta(days=70))
    Tag.objects.filter(pk=tag_c.pk).update(updated_at=now - timedelta(days=60))

    # Confirm the active set is {A, B}, not C.
    from django.db.models import Count, Max

    from apps.mobile.versioning import active_member_tags

    active_ids = set(active_member_tags().values_list("id", flat=True))
    assert tag_c.id not in active_ids
    assert tag_b.id in active_ids

    # Confirm A holds MAX(updated_at) over the active set before the swap.
    tag_a.refresh_from_db()
    agg_before = active_member_tags().aggregate(
        max_ua=Max("updated_at"), cnt=Count("id")
    )
    assert agg_before["max_ua"] == tag_a.updated_at
    assert agg_before["cnt"] == 2

    before = member_tags_version()

    # Touch tag_c: advances MAX(last_seen_at) so tag_b falls below the new floor
    # and tag_c enters. COUNT stays 2; MAX(updated_at) stays at tag_a's value.
    # A naive MAX|COUNT fingerprint would produce the same hash and return a
    # stale 304.
    tag_c.last_seen_at = now + timedelta(days=2)
    tag_c.save(update_fields=["last_seen_at"])

    # After the swap active set is {A, C}, not {A, B}.
    active_ids_after = set(active_member_tags().values_list("id", flat=True))
    assert tag_b.id not in active_ids_after
    assert tag_c.id in active_ids_after

    # Confirm MAX(updated_at) and COUNT are unchanged — only identities changed.
    # This is the exact scenario the old MAX|COUNT fingerprint would miss.
    agg_after = active_member_tags().aggregate(
        max_ua=Max("updated_at"), cnt=Count("id")
    )
    assert (
        agg_after["max_ua"] == agg_before["max_ua"]
    ), "MAX(updated_at) must not change"
    assert agg_after["cnt"] == agg_before["cnt"], "COUNT must not change"

    assert member_tags_version() != before


@pytest.mark.django_db
def test_member_tags_version_changes_on_field_edit_when_updated_at_not_max():
    """Field change is detected even when the tag's updated_at is below MAX(updated_at).

    Simulates concurrent provisioning: Tag A's write carries an earlier timestamp
    but commits after Tag B's write. MAX(updated_at) stays at Tag B's value, so a
    fingerprint based only on MAX|COUNT|IDs would silently return stale data.
    Hashing (id, number, nfc_uid) detects the change regardless.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.versioning import member_tags_version
    from website.models.tag import Tag

    now = timezone.now()

    # Tag B anchors MAX(updated_at) at a later time.
    Tag.objects.create(number=2, nfc_uid="BB02", last_seen_at=now)
    tag_a = Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=now)
    # Backdate tag A so its updated_at is below tag B's (simulates the
    # earlier-timestamp write).
    Tag.objects.filter(pk=tag_a.pk).update(updated_at=now - timedelta(days=5))

    before = member_tags_version()

    # Simulate the late-committing earlier-timestamped transaction: change the field
    # without touching updated_at (bypass auto_now via queryset.update).
    Tag.objects.filter(pk=tag_a.pk).update(number=99)

    # MAX(updated_at) and COUNT are still unchanged — only the field value differs.
    # The fingerprint must still change.
    assert member_tags_version() != before


# --- MemberTagsView request-level -------------------------------------------


@pytest.mark.django_db
def test_member_tags_valid_signature_returns_200_with_fields(client, settings):
    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="MT race", slug="mt-race")
    Tag.objects.create(number=7, nfc_uid="aa01")

    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert list(data.keys()) == ["member_tags"]
    assert len(data["member_tags"]) == 1
    entry = data["member_tags"][0]
    assert set(entry.keys()) == {"number", "nfc_uid"}
    assert entry["number"] == 7
    assert entry["nfc_uid"] == "AA01"


@pytest.mark.django_db
def test_member_tags_no_headers_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    race, _ = _make_race_with_category(slug="mt-403")
    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_member_tags_wrong_signature_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="mt-bad-sig")
    path = f"/app/race/{race.id}/member_tags/"
    headers = _signed_headers("GET", path, "wrong-secret")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_member_tags_unknown_key_id_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="mt-bad-key")
    path = f"/app/race/{race.id}/member_tags/"
    headers = _signed_headers("GET", path, SECRET, key_id="not-in-map")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_member_tags_valid_sig_nonexistent_race_returns_404(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    path = "/app/race/999999/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_member_tags_unpublished_race_returns_404(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race = Race.objects.create(name="Unpub", slug="unpub-mt", is_published=False)
    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 404


@pytest.mark.django_db
def test_member_tags_response_carries_etag(client, settings):
    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="MT etag", slug="mt-etag")
    Tag.objects.create(number=1, nfc_uid="AA01")

    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    etag = response["ETag"]
    assert etag.startswith('"') and etag.endswith('"')


@pytest.mark.django_db
def test_member_tags_if_none_match_returns_304_empty_body(client, settings):
    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="MT 304", slug="mt-304")
    Tag.objects.create(number=1, nfc_uid="AA01")

    path = f"/app/race/{race.id}/member_tags/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    etag = first["ETag"]

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = etag
    second = client.get(path, **headers)

    assert second.status_code == 304
    assert second["ETag"] == etag
    assert second.content == b""


@pytest.mark.django_db
def test_member_tags_stale_if_none_match_returns_200_with_new_etag(client, settings):
    from django.utils import timezone

    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="MT stale", slug="mt-stale")
    tag = Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=timezone.now())

    path = f"/app/race/{race.id}/member_tags/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    old_etag = first["ETag"]

    # A provisioning renumber moves the fingerprint.
    tag.number = 99
    tag.save()

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = old_etag
    second = client.get(path, **headers)

    assert second.status_code == 200
    assert second["ETag"] != old_etag


@pytest.mark.django_db
def test_member_tags_window_excludes_old_includes_fresh(client, settings):
    """The served set is the data-anchored 30-day window (explicit last_seen_at)."""
    from datetime import timedelta

    from django.utils import timezone

    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="MT window", slug="mt-window")
    now = timezone.now()
    Tag.objects.create(number=1, nfc_uid="AA01", last_seen_at=now)
    Tag.objects.create(number=2, nfc_uid="AA02", last_seen_at=now - timedelta(days=29))
    Tag.objects.create(number=3, nfc_uid="AA03", last_seen_at=now - timedelta(days=31))

    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    numbers = {t["number"] for t in response.json()["member_tags"]}
    assert numbers == {1, 2}  # the 31-day-old chip is aged past the floor


@pytest.mark.django_db
def test_member_tags_never_scanned_returns_all(client, settings):
    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="MT never", slug="mt-never")
    Tag.objects.create(number=1, nfc_uid="AA01")
    Tag.objects.create(number=2, nfc_uid="AA02")

    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    numbers = {t["number"] for t in response.json()["member_tags"]}
    assert numbers == {1, 2}


@pytest.mark.django_db
def test_member_tags_tampered_query_string_returns_403(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race, _ = _make_race_with_category(slug="mt-qs")
    path = f"/app/race/{race.id}/member_tags/"
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path + "?foo=bar", **headers)
    assert response.status_code == 403


@pytest.mark.django_db
def test_sync_versions_member_tags_matches_member_tags_etag(client, settings):
    from website.models.race import Race
    from website.models.tag import Tag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Sync member tags", slug="sync-mt-etag")
    Tag.objects.create(number=11, nfc_uid="bb02")

    mt_path = f"/app/race/{race.id}/member_tags/"
    mt_resp = client.get(mt_path, **_signed_headers("GET", mt_path, SECRET))
    etag = mt_resp["ETag"]

    sync_path = f"/app/race/{race.id}/sync/"
    sync_resp = client.get(sync_path, **_signed_headers("GET", sync_path, SECRET))

    versions = sync_resp.json()["versions"]
    assert "member_tags" in versions
    bare = versions["member_tags"]
    # /member_tags/ ETag is the bare version wrapped in quotes
    assert etag == f'"{bare}"'


@pytest.mark.django_db
def test_member_tags_records_appinstall(client, settings):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race = Race.objects.create(name="MT install", slug="mt-install")
    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200
    install = AppInstall.objects.get(install_id="install-abc")
    assert install.request_count == 1


@pytest.mark.django_db
def test_member_tags_stats_write_failure_does_not_break_response(
    client, settings, monkeypatch
):
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    race = Race.objects.create(name="MT stats fail", slug="mt-stats-fail")

    def boom(*args, **kwargs):
        raise IntegrityError("simulated stats write failure")

    monkeypatch.setattr(AppInstall.objects, "update_or_create", boom)

    path = f"/app/race/{race.id}/member_tags/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert not AppInstall.objects.filter(install_id="install-abc").exists()


# --- Task 1: MobileToken model + token helpers ------------------------------


def test_tokens_generate_hash_roundtrip():
    from apps.mobile.tokens import generate_token, hash_token

    raw, token_hash = generate_token()
    assert isinstance(raw, str) and raw
    # token_hash is the sha256 hex of the raw token (64 hex chars).
    assert token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert len(token_hash) == 64
    assert hash_token(raw) == token_hash
    # Two mints differ (high entropy).
    other_raw, _ = generate_token()
    assert other_raw != raw


@pytest.mark.django_db
def test_mobile_token_is_active_for_fresh(django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token

    user = django_user_model.objects.create_user(
        username="u-active", email="active@example.com", password="x"
    )
    _, token_hash = generate_token()
    token = MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() + timedelta(days=30),
    )
    assert token.is_active is True


@pytest.mark.django_db
def test_mobile_token_is_active_false_when_expired(django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token

    user = django_user_model.objects.create_user(
        username="u-exp", email="exp@example.com", password="x"
    )
    _, token_hash = generate_token()
    token = MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert token.is_active is False


@pytest.mark.django_db
def test_mobile_token_is_active_false_when_revoked(django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token

    user = django_user_model.objects.create_user(
        username="u-rev", email="rev@example.com", password="x"
    )
    _, token_hash = generate_token()
    token = MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() + timedelta(days=30),
        revoked_at=timezone.now(),
    )
    assert token.is_active is False


@pytest.mark.django_db
def test_resolve_token_returns_row_and_stamps_last_used(django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token, resolve_token

    user = django_user_model.objects.create_user(
        username="u-resolve", email="resolve@example.com", password="x"
    )
    raw, token_hash = generate_token()
    token = MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() + timedelta(days=30),
    )
    assert token.last_used_at is None

    resolved = resolve_token(raw)
    assert resolved is not None
    assert resolved.pk == token.pk
    token.refresh_from_db()
    assert token.last_used_at is not None


@pytest.mark.django_db
def test_resolve_token_none_for_unknown():
    from apps.mobile.tokens import resolve_token

    assert resolve_token("no-such-token") is None
    assert resolve_token("") is None
    assert resolve_token(None) is None


@pytest.mark.django_db
def test_resolve_token_none_for_expired(django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token, resolve_token

    user = django_user_model.objects.create_user(
        username="u-resolve-exp", email="resolve-exp@example.com", password="x"
    )
    raw, token_hash = generate_token()
    MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert resolve_token(raw) is None


@pytest.mark.django_db
def test_resolve_token_none_for_revoked(django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token, resolve_token

    user = django_user_model.objects.create_user(
        username="u-resolve-rev", email="resolve-rev@example.com", password="x"
    )
    raw, token_hash = generate_token()
    MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() + timedelta(days=30),
        revoked_at=timezone.now(),
    )
    assert resolve_token(raw) is None


@pytest.mark.django_db
def test_resolve_token_none_for_deactivated_user(django_user_model):
    """A live token whose owner was deactivated must not resolve."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token, resolve_token

    user = django_user_model.objects.create_user(
        username="u-deactivated", email="deactivated@example.com", password="x"
    )
    raw, token_hash = generate_token()
    MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() + timedelta(days=30),
    )
    # Token itself is still active; only the user is disabled.
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert resolve_token(raw) is None


# --- Task 2: POST /app/login/ -----------------------------------------------

LOGIN_PATH = "/app/login/"


def _signed_post(client, path, secret, body_bytes, key_id="test-v1"):
    """POST a JSON body with a build-HMAC signature over that exact body."""
    headers = _signed_headers("POST", path, secret, body=body_bytes, key_id=key_id)
    return client.post(
        path, data=body_bytes, content_type="application/json", **headers
    )


@pytest.mark.django_db
def test_login_valid_creds_returns_token(client, settings, django_user_model):
    import json

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import hash_token

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="crew1", email="crew1@example.com", password="s3cret-pass"
    )
    body = json.dumps(
        {"email": "crew1@example.com", "password": "s3cret-pass"}
    ).encode()
    response = _signed_post(client, LOGIN_PATH, SECRET, body)

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"token", "expires_at"}
    raw = data["token"]
    assert raw

    # exactly one token row, storing only the hash (raw never persisted)
    assert MobileToken.objects.count() == 1
    token = MobileToken.objects.get()
    assert token.token_hash == hash_token(raw)
    assert not MobileToken.objects.filter(token_hash=raw).exists()
    assert token.is_active


@pytest.mark.django_db
def test_login_case_insensitive_email(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="crewmixed", email="Mixed@Example.com", password="pw-123456"
    )
    body = json.dumps({"email": "mixed@example.com", "password": "pw-123456"}).encode()
    response = _signed_post(client, LOGIN_PATH, SECRET, body)
    assert response.status_code == 200


@pytest.mark.django_db
def test_login_wrong_password_returns_401_generic(client, settings, django_user_model):
    import json

    from apps.mobile.models import MobileToken

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="crew2", email="crew2@example.com", password="right-pass"
    )
    body = json.dumps({"email": "crew2@example.com", "password": "WRONG"}).encode()
    response = _signed_post(client, LOGIN_PATH, SECRET, body)

    assert response.status_code == 401
    assert response.json() == {"detail": "Неверный email или пароль"}
    assert MobileToken.objects.count() == 0


@pytest.mark.django_db
def test_login_unknown_email_returns_same_401(client, settings):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    body = json.dumps({"email": "ghost@example.com", "password": "whatever"}).encode()
    response = _signed_post(client, LOGIN_PATH, SECRET, body)

    # Same status + message as a wrong password: no account enumeration.
    assert response.status_code == 401
    assert response.json() == {"detail": "Неверный email или пароль"}


@pytest.mark.django_db
def test_login_missing_build_signature_returns_403(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="crew3", email="crew3@example.com", password="pw-123456"
    )
    body = json.dumps({"email": "crew3@example.com", "password": "pw-123456"}).encode()
    # No signed headers at all → neutral build-layer 403.
    response = client.post(LOGIN_PATH, data=body, content_type="application/json")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_login_bad_build_signature_returns_403(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="crew4", email="crew4@example.com", password="pw-123456"
    )
    body = json.dumps({"email": "crew4@example.com", "password": "pw-123456"}).encode()
    response = _signed_post(client, LOGIN_PATH, "wrong-secret", body)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_login_missing_email_field_returns_400(client, settings):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    body = json.dumps({"password": "pw-123456"}).encode()
    response = _signed_post(client, LOGIN_PATH, SECRET, body)
    # Serializer validation failure → 400, not a 500 and not a 401.
    assert response.status_code == 400


@pytest.mark.django_db
def test_login_non_json_body_returns_400(client, settings):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    body = b"not-json-at-all"
    response = _signed_post(client, LOGIN_PATH, SECRET, body)
    # Malformed JSON parsed by DRF → 400 (no 500, no throttle crash).
    assert response.status_code == 400


@pytest.mark.django_db
def test_login_blank_email_returns_400(client, settings):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    body = json.dumps({"email": "", "password": "pw-123456"}).encode()
    response = _signed_post(client, LOGIN_PATH, SECRET, body)
    assert response.status_code == 400


@pytest.mark.django_db
def test_login_throttle_returns_429_after_limit(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="crew5", email="crew5@example.com", password="right-pass"
    )
    body = json.dumps({"email": "crew5@example.com", "password": "WRONG"}).encode()

    statuses = []
    for _ in range(7):  # rate is 5/min
        resp = _signed_post(client, LOGIN_PATH, SECRET, body)
        statuses.append(resp.status_code)

    # The first 5 are allowed (401 here, wrong password); subsequent ones throttle.
    assert statuses[:5] == [401] * 5
    assert 429 in statuses[5:]


# --- Task 3: IsMobileUser permission + POST /app/logout/ --------------------

LOGOUT_PATH = "/app/logout/"


def _make_active_token(user):
    """Create an active MobileToken for ``user`` and return its raw value."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token

    raw, token_hash = generate_token()
    MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return raw


def _signed_post_auth(client, path, secret, body_bytes, bearer, key_id="test-v1"):
    """Signed POST that also carries an ``Authorization: Bearer`` header."""
    headers = _signed_headers("POST", path, secret, body=body_bytes, key_id=key_id)
    if bearer is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {bearer}"
    return client.post(
        path, data=body_bytes, content_type="application/json", **headers
    )


@pytest.mark.django_db
def test_logout_valid_token_returns_200_and_revokes(
    client, settings, django_user_model
):
    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import hash_token

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="lo1", email="lo1@example.com", password="x"
    )
    raw = _make_active_token(user)

    response = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", raw)
    assert response.status_code == 200

    token = MobileToken.objects.get(token_hash=hash_token(raw))
    assert token.revoked_at is not None
    assert token.is_active is False


@pytest.mark.django_db
def test_logout_missing_bearer_returns_401(client, settings, django_user_model):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    # Valid build signature but no Authorization header → actionable 401.
    response = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", None)
    assert response.status_code == 401


@pytest.mark.django_db
def test_logout_malformed_authorization_returns_401(
    client, settings, django_user_model
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="lo-mal", email="lo-mal@example.com", password="x"
    )
    raw = _make_active_token(user)

    # Token value present but no "Bearer " scheme prefix → 401.
    headers = _signed_headers("POST", LOGOUT_PATH, SECRET, body=b"")
    headers["HTTP_AUTHORIZATION"] = raw  # no scheme
    response = client.post(
        LOGOUT_PATH, data=b"", content_type="application/json", **headers
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_logout_expired_token_returns_401(client, settings, django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import generate_token

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="lo-exp", email="lo-exp@example.com", password="x"
    )
    raw, token_hash = generate_token()
    MobileToken.objects.create(
        user=user,
        token_hash=token_hash,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    response = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", raw)
    assert response.status_code == 401


@pytest.mark.django_db
def test_logout_revoked_token_returns_401(client, settings, django_user_model):
    from django.utils import timezone

    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import hash_token

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="lo-rev", email="lo-rev@example.com", password="x"
    )
    raw = _make_active_token(user)
    MobileToken.objects.filter(token_hash=hash_token(raw)).update(
        revoked_at=timezone.now()
    )

    response = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", raw)
    assert response.status_code == 401


@pytest.mark.django_db
def test_logout_missing_build_signature_returns_403(
    client, settings, django_user_model
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="lo-403", email="lo-403@example.com", password="x"
    )
    raw = _make_active_token(user)

    # No build-HMAC headers at all → the build layer rejects first (neutral 403),
    # never reaching the token layer, even with a valid bearer.
    response = client.post(
        LOGOUT_PATH,
        data=b"",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_logout_revokes_only_presented_token(client, settings, django_user_model):
    from apps.mobile.models import MobileToken
    from apps.mobile.tokens import hash_token

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="lo-multi", email="lo-multi@example.com", password="x"
    )
    raw_a = _make_active_token(user)
    raw_b = _make_active_token(user)

    response = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", raw_a)
    assert response.status_code == 200

    token_a = MobileToken.objects.get(token_hash=hash_token(raw_a))
    token_b = MobileToken.objects.get(token_hash=hash_token(raw_b))
    assert token_a.is_active is False
    assert token_b.is_active is True

    # The other token still works for a fresh logout.
    again = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", raw_b)
    assert again.status_code == 200


@pytest.mark.django_db
def test_is_mobile_user_sets_request_mobile_user(settings, django_user_model):
    """Identity layer resolves the bearer to request.mobile_user (+ mobile_token)."""
    from apps.mobile.permissions import IsMobileUser

    user = django_user_model.objects.create_user(
        username="ident", email="ident@example.com", password="x"
    )
    raw = _make_active_token(user)

    request = RequestFactory().post(LOGOUT_PATH, HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert IsMobileUser().has_permission(request, None) is True
    assert request.mobile_user == user
    assert request.mobile_token.user == user


@pytest.mark.django_db
def test_is_mobile_user_raises_401_for_unknown_token(settings):
    from apps.mobile.permissions import IsMobileUser, MobileTokenInvalid

    request = RequestFactory().post(
        LOGOUT_PATH, HTTP_AUTHORIZATION="Bearer no-such-token"
    )
    with pytest.raises(MobileTokenInvalid) as exc:
        IsMobileUser().has_permission(request, None)
    assert exc.value.status_code == 401


# --- Task 4: CanEditRaceLegend permission -----------------------------------
# Tested in isolation here (RequestFactory + the permission object with a stub
# view carrying .kwargs). The Task 6 write endpoint exercises the full stack.


class _StubView:
    """Minimal stand-in for a DRF view exposing only ``kwargs`` to a permission."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _race_admin_request(user):
    """A request that already passed IsMobileUser (mobile_user resolved)."""
    request = RequestFactory().post("/app/race/1/tags/")
    request.mobile_user = user
    return request


@pytest.mark.django_db
def test_can_edit_race_legend_superuser_passes(django_user_model):
    from apps.mobile.permissions import CanEditRaceLegend
    from website.models.race import Race

    su = django_user_model.objects.create_superuser(
        username="su", email="su@example.com", password="x"
    )
    race = Race.objects.create(name="Legend race", slug="legend-race-su")

    request = _race_admin_request(su)
    view = _StubView(race_id=race.id)
    assert CanEditRaceLegend().has_permission(request, view) is True


@pytest.mark.django_db
def test_can_edit_race_legend_raceadmin_passes(django_user_model):
    from apps.mobile.permissions import CanEditRaceLegend
    from website.models.race import Race, RaceAdmin

    user = django_user_model.objects.create_user(
        username="ra", email="ra@example.com", password="x"
    )
    race = Race.objects.create(name="Legend race", slug="legend-race-ra")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    request = _race_admin_request(user)
    view = _StubView(race_id=race.id)
    assert CanEditRaceLegend().has_permission(request, view) is True


@pytest.mark.django_db
def test_can_edit_race_legend_moderator_denied(django_user_model):
    """A MODERATOR is not an editor — can_edit_race requires role=ADMIN."""
    from apps.mobile.permissions import CanEditRaceLegend
    from website.models.race import Race, RaceAdmin

    user = django_user_model.objects.create_user(
        username="mod", email="mod@example.com", password="x"
    )
    race = Race.objects.create(name="Legend race", slug="legend-race-mod")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.MODERATOR)

    request = _race_admin_request(user)
    view = _StubView(race_id=race.id)
    assert CanEditRaceLegend().has_permission(request, view) is False


@pytest.mark.django_db
def test_can_edit_race_legend_plain_user_denied(django_user_model):
    from apps.mobile.permissions import CanEditRaceLegend
    from website.models.race import Race

    user = django_user_model.objects.create_user(
        username="plain", email="plain@example.com", password="x"
    )
    race = Race.objects.create(name="Legend race", slug="legend-race-plain")

    request = _race_admin_request(user)
    view = _StubView(race_id=race.id)
    assert CanEditRaceLegend().has_permission(request, view) is False


@pytest.mark.django_db
def test_can_edit_race_legend_unknown_race_raises_404(django_user_model):
    from django.http import Http404

    from apps.mobile.permissions import CanEditRaceLegend

    su = django_user_model.objects.create_superuser(
        username="su404", email="su404@example.com", password="x"
    )
    request = _race_admin_request(su)
    view = _StubView(race_id=999999)
    with pytest.raises(Http404):
        CanEditRaceLegend().has_permission(request, view)


@pytest.mark.django_db
def test_can_edit_race_legend_no_mobile_user_returns_false_not_500(django_user_model):
    """Defensive: missing request.mobile_user → False (403), not AttributeError."""
    from apps.mobile.permissions import CanEditRaceLegend
    from website.models.race import Race

    race = Race.objects.create(name="Legend race", slug="legend-race-anon")
    # No mobile_user attribute set on the request (stack reordered / isolated).
    request = RequestFactory().post("/app/race/1/tags/")
    view = _StubView(race_id=race.id)
    assert CanEditRaceLegend().has_permission(request, view) is False


# --- Task 5: CheckpointTag.created_by ---------------------------------------


@pytest.mark.django_db
def test_checkpoint_tag_created_by_persists(django_user_model):
    """``created_by`` round-trips and SET_NULL survives the user's deletion."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    user = django_user_model.objects.create_user(
        username="crew", email="crew@example.com", password="x"
    )
    race = Race.objects.create(name="Prov race", slug="prov-race")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", created_by=user
    )

    tag.refresh_from_db()
    assert tag.created_by_id == user.id
    assert tag in user.provisioned_tags.all()

    user.delete()
    tag.refresh_from_db()
    assert tag.created_by_id is None  # on_delete=SET_NULL


@pytest.mark.django_db
def test_checkpoint_tag_created_by_does_not_disturb_crypto_signals(
    django_user_model,
):
    """Adding ``created_by`` must not break the legend-crypto ``post_save``
    signal: a locked-КП tag still gets ``code``/``bid``/``bundle_blob`` and the
    recursion guard (sentinel ``update_fields``) is intact.
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    user = django_user_model.objects.create_user(
        username="crew2", email="crew2@example.com", password="x"
    )
    race = Race.objects.create(name="Prov race 2", slug="prov-race-2")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=4, description="столб", is_legend_locked=True
    )
    tag = CheckpointTag.objects.create(
        checkpoint=cp, nfc_uid="04A1B2C3", created_by=user
    )

    tag.refresh_from_db()
    assert tag.created_by_id == user.id
    assert tag.code is not None
    assert tag.bid == hashlib.sha256(bytes(tag.code)).hexdigest()[:16]
    assert tag.bundle_blob is not None


# --- Task 6: POST /app/race/<race_id>/tags/ ---------------------------------


def _make_admin_race(django_user_model, slug, locked=False):
    """A published race + ADMIN RaceAdmin user + their active token + a КП.

    Returns ``(race, user, raw_token, checkpoint)``.
    """
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race, RaceAdmin

    race = Race.objects.create(name=f"Tag race {slug}", slug=slug)
    user = django_user_model.objects.create_user(
        username=f"crew-{slug}", email=f"crew-{slug}@example.com", password="x"
    )
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    raw = _make_active_token(user)
    cp = Checkpoint.objects.create(
        race=race, number=7, cost=4, description="столб", is_legend_locked=locked
    )
    return race, user, raw, cp


def _tags_path(race_id):
    return f"/app/race/{race_id}/tags/"


@pytest.mark.django_db
def test_tag_create_happy_path_201_with_crypto_via_signals(
    client, settings, django_user_model
):
    import json

    from apps.mobile.models import MobileToken  # noqa: F401
    from website.models.checkpoint import CheckpointTag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "happy", locked=True)
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()

    response = _signed_post_auth(client, path, SECRET, body, raw)

    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {"bid", "checkpoint_id", "number", "nfc_uid", "code"}
    assert data["checkpoint_id"] == cp.id
    assert data["number"] == cp.number
    assert data["nfc_uid"] == "04A1B2C3"

    tag = CheckpointTag.objects.get(checkpoint=cp, nfc_uid="04A1B2C3")
    assert tag.created_by_id == user.id
    # crypto populated by the post_save signal
    assert tag.code is not None
    assert tag.bid == hashlib.sha256(bytes(tag.code)).hexdigest()[:16]
    assert tag.bundle_blob is not None
    # response echoes the freshly minted code (hex) + bid
    assert data["bid"] == tag.bid
    assert data["code"] == bytes(tag.code).hex()


@pytest.mark.django_db
def test_tag_create_idempotent_same_uid_same_cp_no_duplicate(
    client, settings, django_user_model
):
    import json

    from website.models.checkpoint import CheckpointTag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "idem")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()

    first = _signed_post_auth(client, path, SECRET, body, raw)
    assert first.status_code == 201

    second = _signed_post_auth(client, path, SECRET, body, raw)
    assert second.status_code == 200

    assert CheckpointTag.objects.filter(checkpoint=cp, nfc_uid="04A1B2C3").count() == 1
    # the idempotent hit returns the same bid/code as the create
    assert second.json()["bid"] == first.json()["bid"]
    assert second.json()["code"] == first.json()["code"]
    assert second.json()["checkpoint_id"] == cp.id
    assert second.json()["number"] == cp.number


@pytest.mark.django_db
def test_tag_create_same_uid_different_cp_returns_409(
    client, settings, django_user_model
):
    import json

    from website.models.checkpoint import Checkpoint, CheckpointTag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "xcp")
    other_cp = Checkpoint.objects.create(
        race=race, number=8, cost=1, description="other"
    )
    path = _tags_path(race.id)

    first = _signed_post_auth(
        client,
        path,
        SECRET,
        json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode(),
        raw,
    )
    assert first.status_code == 201

    conflict = _signed_post_auth(
        client,
        path,
        SECRET,
        json.dumps({"checkpoint_id": other_cp.id, "nfc_uid": "04A1B2C3"}).encode(),
        raw,
    )
    assert conflict.status_code == 409
    # no rebind: the chip stays on the original КП only
    assert CheckpointTag.objects.filter(nfc_uid="04A1B2C3").count() == 1
    assert CheckpointTag.objects.get(nfc_uid="04A1B2C3").checkpoint_id == cp.id


@pytest.mark.django_db
def test_tag_create_idempotent_hit_on_unbuilt_tag_rebuilds_no_500(
    client, settings, django_user_model
):
    """An existing tag with bid=''/code=None (created bypassing signals) is
    rebuilt on the idempotent hit rather than crashing on None.hex()."""
    import json

    from website.models.checkpoint import CheckpointTag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "unbuilt")
    # Bypass the build_bundle signal so the row keeps bid=""/code=None.
    CheckpointTag.objects.bulk_create(
        [CheckpointTag(checkpoint=cp, nfc_uid="04A1B2C3")]
    )
    assert CheckpointTag.objects.filter(checkpoint=cp, bid="").count() == 1

    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)

    assert response.status_code == 200
    data = response.json()
    tag = CheckpointTag.objects.get(checkpoint=cp, nfc_uid="04A1B2C3")
    assert tag.bid != ""
    assert tag.code is not None
    assert data["bid"] == tag.bid
    assert data["code"] == bytes(tag.code).hex()
    assert data["checkpoint_id"] == cp.id
    assert data["number"] == cp.number


@pytest.mark.django_db
def test_tag_create_unknown_checkpoint_returns_404(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "nocp")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": 999999, "nfc_uid": "04A1B2C3"}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 404


@pytest.mark.django_db
def test_tag_create_checkpoint_in_other_race_returns_404(
    client, settings, django_user_model
):
    import json

    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "othrace")
    other_race = Race.objects.create(name="Other", slug="other-tag-race")
    other_cp = Checkpoint.objects.create(
        race=other_race, number=1, cost=1, description="elsewhere"
    )
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": other_cp.id, "nfc_uid": "04A1B2C3"}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 404


@pytest.mark.django_db
def test_tag_create_hidden_checkpoint_returns_404(client, settings, django_user_model):
    import json

    from website.models.checkpoint import Checkpoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "hidden")
    hidden = Checkpoint.objects.create(
        race=race, number=9, cost=0, description="hidden", type="hidden"
    )
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": hidden.id, "nfc_uid": "04A1B2C3"}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 404


@pytest.mark.django_db
def test_tag_create_normalizes_nfc_uid(client, settings, django_user_model):
    import json

    from website.models.checkpoint import CheckpointTag

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "norm")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "  04a1b2c3 "}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)

    assert response.status_code == 201
    assert response.json()["nfc_uid"] == "04A1B2C3"
    assert CheckpointTag.objects.filter(checkpoint=cp, nfc_uid="04A1B2C3").exists()


@pytest.mark.django_db
def test_tag_create_blank_nfc_uid_returns_400(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "blank")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "   "}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 400


@pytest.mark.django_db
def test_tag_create_oversized_nfc_uid_returns_400(client, settings, django_user_model):
    """A UID longer than the model's 255-char column is a clean 400, not a 500."""
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "oversize")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "A" * 256}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 400


@pytest.mark.django_db
def test_tag_create_missing_bearer_returns_401(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "nobearer")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()
    # valid build sig but no Authorization header → actionable 401
    response = _signed_post_auth(client, path, SECRET, body, None)
    assert response.status_code == 401


@pytest.mark.django_db
def test_tag_create_missing_build_signature_returns_403(
    client, settings, django_user_model
):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "nosig")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()
    response = client.post(
        path,
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_tag_create_non_admin_user_returns_403(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Tag race plain", slug="tag-race-plain")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")
    # a valid user + token, but no RaceAdmin row → CanEditRaceLegend denies
    user = django_user_model.objects.create_user(
        username="plain-crew", email="plain-crew@example.com", password="x"
    )
    raw = _make_active_token(user)

    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 403
    # actionable (not the neutral build-layer "Forbidden")
    assert response.json() != {"detail": "Forbidden"}


@pytest.mark.django_db
def test_tag_create_unpublished_race_returns_404(client, settings, django_user_model):
    import json

    from website.models.checkpoint import Checkpoint
    from website.models.race import Race, RaceAdmin

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Unpub tag", slug="unpub-tag", is_published=False)
    user = django_user_model.objects.create_user(
        username="unpub-crew", email="unpub-crew@example.com", password="x"
    )
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    raw = _make_active_token(user)
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="x")

    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()
    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 404


@pytest.mark.django_db
def test_tag_create_moves_legend_version_and_etag(client, settings, django_user_model):
    """Creating a tag bumps versions.legend; the new ETag 304s, the old 200s."""
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "etagmove")
    legend_path = f"/app/race/{race.id}/legend/"

    before = client.get(legend_path, **_signed_headers("GET", legend_path, SECRET))
    old_etag = before["ETag"]

    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()
    created = _signed_post_auth(client, path, SECRET, body, raw)
    assert created.status_code == 201

    after = client.get(legend_path, **_signed_headers("GET", legend_path, SECRET))
    new_etag = after["ETag"]
    assert new_etag != old_etag

    # the new ETag short-circuits to 304
    headers = _signed_headers("GET", legend_path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = new_etag
    fresh = client.get(legend_path, **headers)
    assert fresh.status_code == 304

    # the stale (old) ETag does not
    headers = _signed_headers("GET", legend_path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = old_etag
    stale = client.get(legend_path, **headers)
    assert stale.status_code == 200


@pytest.mark.django_db
def test_tag_create_throttle_returns_429_after_limit(
    client, settings, django_user_model
):
    """``mobile-write`` ScopedRateThrottle fires 429 when the rate is exceeded."""
    import json

    from rest_framework.throttling import SimpleRateThrottle

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "throttle-write")

    # DRF caches THROTTLE_RATES as a class attribute at import time, so settings
    # overrides don't propagate. Patch the class directly for the duration of the
    # test (autouse fixture already clears the cache counts).
    original_rates = SimpleRateThrottle.THROTTLE_RATES
    SimpleRateThrottle.THROTTLE_RATES = {**original_rates, "mobile-write": "2/min"}
    try:
        statuses = []
        for i in range(4):
            body = json.dumps(
                {"checkpoint_id": cp.id, "nfc_uid": f"AA{i:06X}"}
            ).encode()
            resp = _signed_post_auth(client, _tags_path(race.id), SECRET, body, raw)
            statuses.append(resp.status_code)
    finally:
        SimpleRateThrottle.THROTTLE_RATES = original_rates

    assert 429 in statuses[2:]


# --- Task 7: body-signing roundtrip on POST -------------------------------
#
# These tests pin the previously-GET-only body-signing contract for POST.
# `SignedAppPermission` reads `request.body` to rebuild the canonical string;
# the view then reads `request.data` (DRF JSON parse). Django/DRF buffer the
# body, so reading it in the permission does NOT raise `RawPostDataException`
# in the view — the roundtrip below proves that end to end (a 200/201 means the
# serializer parsed `request.data` after the permission consumed `request.body`).


@pytest.mark.django_db
def test_post_body_signature_roundtrip_passes_and_parses_data(
    client, settings, django_user_model
):
    """Correct body-inclusive signature → permission passes AND `request.data`
    parses (no `RawPostDataException` after the body read)."""
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="bsr1", email="bsr1@example.com", password="pw-123456"
    )
    body = json.dumps({"email": "bsr1@example.com", "password": "pw-123456"}).encode()
    headers = _signed_headers("POST", LOGIN_PATH, SECRET, body=body)

    response = client.post(
        LOGIN_PATH, data=body, content_type="application/json", **headers
    )
    # 200 proves both layers: build-HMAC over the body verified, then the view
    # read request.data (the email/password) without RawPostDataException.
    assert response.status_code == 200
    assert "token" in response.json()


@pytest.mark.django_db
def test_post_tampered_body_returns_neutral_403(client, settings, django_user_model):
    """Signature computed over a stale body but a different body sent → the
    `sha256_hex(body)` term mismatches → neutral build-layer 403 (no hint)."""
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    django_user_model.objects.create_user(
        username="bsr2", email="bsr2@example.com", password="pw-123456"
    )
    signed_body = json.dumps(
        {"email": "bsr2@example.com", "password": "pw-123456"}
    ).encode()
    headers = _signed_headers("POST", LOGIN_PATH, SECRET, body=signed_body)

    # Send a *different* body than the one the signature covers.
    tampered_body = json.dumps(
        {"email": "bsr2@example.com", "password": "TAMPERED"}
    ).encode()
    response = client.post(
        LOGIN_PATH, data=tampered_body, content_type="application/json", **headers
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_post_empty_body_signature_handled(client, settings, django_user_model):
    """Empty-body POST (logout) signed over `b""` verifies — the empty-body
    branch of the canonical (`sha256_hex(b"")`) is handled the same as GET."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    user = django_user_model.objects.create_user(
        username="bsr3", email="bsr3@example.com", password="x"
    )
    raw = _make_active_token(user)

    response = _signed_post_auth(client, LOGOUT_PATH, SECRET, b"", raw)
    assert response.status_code == 200


@pytest.mark.django_db
def test_post_present_body_signature_handled(client, settings, django_user_model):
    """Present-body POST (tag create) signed over the JSON body verifies and
    the view parses it — the present-body counterpart to the empty-body case."""
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, user, raw, cp = _make_admin_race(django_user_model, "bsr-present")
    path = _tags_path(race.id)
    body = json.dumps({"checkpoint_id": cp.id, "nfc_uid": "04A1B2C3"}).encode()

    response = _signed_post_auth(client, path, SECRET, body, raw)
    assert response.status_code == 201
    assert response.json()["nfc_uid"] == "04A1B2C3"


def test_signed_permission_reads_body_before_drf_parse():
    """Unit-level pin: the permission's `request.body` read leaves the body
    intact for a subsequent `request.data` parse (Django buffers it), so the
    documented `RawPostDataException` hazard does not bite for JSON POST."""
    import json

    from django.test import override_settings
    from rest_framework.parsers import JSONParser
    from rest_framework.request import Request

    body = json.dumps({"checkpoint_id": 1, "nfc_uid": "AB"}).encode()
    factory = RequestFactory()
    raw_request = factory.post(
        "/app/race/1/tags/", data=body, content_type="application/json"
    )

    ts = str(int(time.time()))
    canonical = build_canonical("POST", "/app/race/1/tags/", ts, body)
    raw_request.META["HTTP_X_APP_KEY_ID"] = "test-v1"
    raw_request.META["HTTP_X_APP_SIG"] = sign(SECRET, canonical)
    raw_request.META["HTTP_X_APP_TS"] = ts
    raw_request.META["HTTP_X_INSTALL_ID"] = "install-abc"

    with override_settings(
        MOBILE_APP_KEYS={"test-v1": SECRET}, MOBILE_APP_TS_WINDOW=300
    ):
        # 1) permission reads request.body to build the canonical
        assert SignedAppPermission().has_permission(raw_request, view=None) is True
        # 2) DRF can still parse the same body afterwards (no RawPostDataException)
        drf_request = Request(raw_request, parsers=[JSONParser()])
        assert drf_request.data == {"checkpoint_id": 1, "nfc_uid": "AB"}


# --- TrackPoint model (Task 1) ---------------------------------------------


def _make_team_in_race(django_user_model, name="Track team", slug="track-race"):
    """Create a published race with a category and one team in it."""
    from website.models.models import Team
    from website.models.race import Category, Race

    race = Race.objects.create(name=name, slug=slug, is_published=True)
    category = Category.objects.create(code="open", name="Open", race=race)
    user = django_user_model.objects.create_user(
        username=f"owner-{slug}", email=f"{slug}@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Alpha")
    return race, team


@pytest.mark.django_db
def test_trackpoint_round_trips_all_fields(django_user_model):
    from apps.mobile.models import TrackPoint

    race, team = _make_team_in_race(django_user_model)
    pk = "11111111-1111-1111-1111-111111111111"
    TrackPoint.objects.create(
        id=pk,
        team=team,
        race=race,
        install_id="install-abc",
        segment_id="seg-1",
        lat=55.75,
        lon=37.62,
        accuracy=4.5,
        altitude=None,
        vertical_accuracy=None,
        gps_time_ms=1700000000000,
        trusted_ms=None,
        elapsed_at=123456,
        boot_count=None,
    )

    tp = TrackPoint.objects.get(pk=pk)
    assert tp.pk == pk
    assert tp.team_id == team.id
    assert tp.race_id == race.id
    assert tp.install_id == "install-abc"
    assert tp.segment_id == "seg-1"
    assert tp.lat == 55.75
    assert tp.lon == 37.62
    assert tp.accuracy == 4.5
    assert tp.altitude is None
    assert tp.vertical_accuracy is None
    assert tp.gps_time_ms == 1700000000000
    assert tp.trusted_ms is None
    assert tp.elapsed_at == 123456
    assert tp.boot_count is None
    assert tp.created_at is not None


@pytest.mark.django_db
def test_trackpoint_round_trips_non_null_optionals(django_user_model):
    from apps.mobile.models import TrackPoint

    race, team = _make_team_in_race(django_user_model, slug="track-race-2")
    TrackPoint.objects.create(
        id="pk-with-optionals",
        team=team,
        race=race,
        install_id="install-abc",
        segment_id="seg-1",
        lat=10.0,
        lon=20.0,
        accuracy=3.0,
        altitude=-12.5,  # below sea level
        vertical_accuracy=2.0,
        gps_time_ms=1,
        trusted_ms=2,
        elapsed_at=3,
        boot_count=7,
    )
    tp = TrackPoint.objects.get(pk="pk-with-optionals")
    assert tp.altitude == -12.5
    assert tp.vertical_accuracy == 2.0
    assert tp.trusted_ms == 2
    assert tp.boot_count == 7


@pytest.mark.django_db
def test_trackpoint_bulk_create_ignore_conflicts_is_idempotent(django_user_model):
    from apps.mobile.models import TrackPoint

    race, team = _make_team_in_race(django_user_model, slug="track-race-3")
    pk = "dup-point-id"

    def _point(lat):
        return TrackPoint(
            id=pk,
            team=team,
            race=race,
            install_id="install-abc",
            segment_id="seg-1",
            lat=lat,
            lon=20.0,
            accuracy=3.0,
            gps_time_ms=1,
            elapsed_at=3,
        )

    TrackPoint.objects.bulk_create([_point(11.0)], ignore_conflicts=True)
    assert TrackPoint.objects.count() == 1

    # second bulk_create with the same id silently no-ops; original row untouched
    TrackPoint.objects.bulk_create([_point(99.0)], ignore_conflicts=True)
    assert TrackPoint.objects.count() == 1
    assert TrackPoint.objects.get(pk=pk).lat == 11.0


@pytest.mark.django_db
def test_trackpoint_create_duplicate_pk_raises(django_user_model):
    from apps.mobile.models import TrackPoint

    race, team = _make_team_in_race(django_user_model, slug="track-race-4")
    TrackPoint.objects.create(
        id="same-pk",
        team=team,
        race=race,
        install_id="install-abc",
        segment_id="seg-1",
        lat=1.0,
        lon=2.0,
        accuracy=3.0,
        gps_time_ms=1,
        elapsed_at=3,
    )
    with pytest.raises(IntegrityError):
        TrackPoint.objects.create(
            id="same-pk",
            team=team,
            race=race,
            install_id="install-abc",
            segment_id="seg-1",
            lat=4.0,
            lon=5.0,
            accuracy=6.0,
            gps_time_ms=1,
            elapsed_at=3,
        )


# --- Track upload serializers (Task 2) -------------------------------------


def _valid_track_point(**overrides):
    """A fully-populated, valid GPS fix dict for serializer tests."""
    point = {
        "id": "11111111-1111-1111-1111-111111111111",
        "segment_id": "seg-1",
        "lat": 55.75,
        "lon": 37.62,
        "accuracy": 4.5,
        "altitude": 120.0,
        "vertical_accuracy": 2.0,
        "gps_time_ms": 1700000000000,
        "trusted_ms": 1700000000001,
        "elapsed_at": 123456,
        "boot_count": 7,
    }
    point.update(overrides)
    return point


def test_track_upload_serializer_valid_batch():
    from apps.mobile.serializers import TrackUploadSerializer

    payload = {"team_id": 42, "points": [_valid_track_point()]}
    serializer = TrackUploadSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    data = serializer.validated_data
    assert data["team_id"] == 42
    assert len(data["points"]) == 1
    assert data["points"][0]["id"] == "11111111-1111-1111-1111-111111111111"
    assert data["points"][0]["lat"] == 55.75


def test_track_upload_serializer_omitted_nullables_resolve_absent():
    from apps.mobile.serializers import TrackUploadSerializer

    point = _valid_track_point()
    for field in ("altitude", "vertical_accuracy", "trusted_ms", "boot_count"):
        point.pop(field)
    serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["points"][0]
    # Omitted optional fields are simply absent (not defaulted to None).
    assert "altitude" not in parsed
    assert "vertical_accuracy" not in parsed
    assert "trusted_ms" not in parsed
    assert "boot_count" not in parsed


def test_track_upload_serializer_explicit_null_nullables():
    from apps.mobile.serializers import TrackUploadSerializer

    point = _valid_track_point(
        altitude=None, vertical_accuracy=None, trusted_ms=None, boot_count=None
    )
    serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["points"][0]
    assert parsed["altitude"] is None
    assert parsed["vertical_accuracy"] is None
    assert parsed["trusted_ms"] is None
    assert parsed["boot_count"] is None


def test_track_upload_serializer_missing_required_field_invalid():
    from apps.mobile.serializers import TrackUploadSerializer

    point = _valid_track_point()
    point.pop("lat")
    serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
    assert not serializer.is_valid()
    assert "points" in serializer.errors


def test_track_upload_serializer_out_of_range_lat_lon_invalid():
    from apps.mobile.serializers import TrackUploadSerializer

    for field, bad in [("lat", 91.0), ("lat", -91.0), ("lon", 181.0), ("lon", -181.0)]:
        point = _valid_track_point(**{field: bad})
        serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
        assert not serializer.is_valid(), f"{field}={bad} should be invalid"


def test_track_upload_serializer_negative_magnitudes_invalid():
    from apps.mobile.serializers import TrackUploadSerializer

    for field in (
        "accuracy",
        "gps_time_ms",
        "elapsed_at",
        "trusted_ms",
        "boot_count",
        "vertical_accuracy",
    ):
        point = _valid_track_point(**{field: -1})
        serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
        assert not serializer.is_valid(), f"negative {field} should be invalid"


def test_track_upload_serializer_empty_id_or_segment_invalid():
    from apps.mobile.serializers import TrackUploadSerializer

    for field in ("id", "segment_id"):
        point = _valid_track_point(**{field: ""})
        serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
        assert not serializer.is_valid(), f"empty {field} should be invalid"


def test_track_upload_serializer_negative_altitude_valid():
    from apps.mobile.serializers import TrackUploadSerializer

    point = _valid_track_point(altitude=-50.0)  # below sea level
    serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["points"][0]["altitude"] == -50.0


def test_track_upload_serializer_empty_points_valid():
    from apps.mobile.serializers import TrackUploadSerializer

    serializer = TrackUploadSerializer(data={"team_id": 1, "points": []})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["points"] == []


def test_track_upload_serializer_over_500_points_invalid():
    from apps.mobile.serializers import TrackUploadSerializer

    points = [_valid_track_point(id=f"pt-{i}") for i in range(501)]
    serializer = TrackUploadSerializer(data={"team_id": 1, "points": points})
    assert not serializer.is_valid()
    assert "points" in serializer.errors


def test_track_upload_serializer_nan_float_fields_invalid():
    """NaN must be rejected for all float GPS fields — it passes min/max validators
    because NaN comparisons always return False in Python."""
    from apps.mobile.serializers import TrackUploadSerializer

    nan_fields = ["lat", "lon", "accuracy", "altitude", "vertical_accuracy"]
    for field in nan_fields:
        point = _valid_track_point(**{field: "NaN"})
        serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
        assert not serializer.is_valid(), f"NaN in {field!r} should be invalid"


def test_track_upload_serializer_infinity_float_fields_invalid():
    """Infinity must be rejected for all float GPS fields — fields without a
    max_value (accuracy, altitude, vertical_accuracy) would otherwise accept inf."""
    from apps.mobile.serializers import TrackUploadSerializer

    inf_fields = ["lat", "lon", "accuracy", "altitude", "vertical_accuracy"]
    for field in inf_fields:
        for inf_str in ["1e309", "-1e309", "Inf", "-Inf"]:
            point = _valid_track_point(**{field: inf_str})
            serializer = TrackUploadSerializer(data={"team_id": 1, "points": [point]})
            assert (
                not serializer.is_valid()
            ), f"Value {inf_str!r} in {field!r} should be invalid (non-finite)"


# --- Track upload endpoint (Task 3) ----------------------------------------


def _track_path(race_id):
    return f"/app/race/{race_id}/track/"


@pytest.mark.django_db
def test_track_upload_wrong_signature_returns_403(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-403")
    path = _track_path(race.id)
    body = json.dumps({"team_id": team.id, "points": [_valid_track_point()]}).encode()
    # build the signature with the WRONG secret → build gate must reject
    response = _signed_post(client, path, "wrong-secret", body)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_track_upload_happy_path_persists_and_acks(client, settings, django_user_model):
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-happy")
    path = _track_path(race.id)
    p1 = _valid_track_point(id="pt-a")
    p2 = _valid_track_point(id="pt-b", lat=10.0, lon=20.0)
    body = json.dumps({"team_id": team.id, "points": [p1, p2]}).encode()

    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 200
    assert response.json() == {"accepted": ["pt-a", "pt-b"]}

    assert TrackPoint.objects.count() == 2
    row = TrackPoint.objects.get(pk="pt-a")
    assert row.race_id == race.id
    assert row.team_id == team.id
    assert row.install_id == "install-abc"  # from the signed header
    assert row.segment_id == "seg-1"
    assert row.lat == 55.75


@pytest.mark.django_db
def test_track_upload_idempotent_no_duplicate_rows(client, settings, django_user_model):
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-idem")
    path = _track_path(race.id)
    body = json.dumps(
        {"team_id": team.id, "points": [_valid_track_point(id="pt-x")]}
    ).encode()

    r1 = _signed_post(client, path, SECRET, body)
    r2 = _signed_post(client, path, SECRET, body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json() == {"accepted": ["pt-x"]}
    assert TrackPoint.objects.count() == 1


@pytest.mark.django_db
def test_track_upload_install_id_stamping(client, settings, django_user_model):
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-install")
    path = _track_path(race.id)

    def _post_with_install(install_id, point_id):
        body = json.dumps(
            {"team_id": team.id, "points": [_valid_track_point(id=point_id)]}
        ).encode()
        # install_id is OUTSIDE the signed canonical (method + path + ts + body),
        # so overriding the header post-signing keeps the signature valid.
        headers = _signed_headers("POST", path, SECRET, body=body)
        headers["HTTP_X_INSTALL_ID"] = install_id
        return client.post(path, data=body, content_type="application/json", **headers)

    r1 = _post_with_install("phone-A", "pt-a")
    r2 = _post_with_install("phone-B", "pt-b")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert TrackPoint.objects.get(pk="pt-a").install_id == "phone-A"
    assert TrackPoint.objects.get(pk="pt-b").install_id == "phone-B"


@pytest.mark.django_db
def test_track_upload_team_not_in_race_returns_404(client, settings, django_user_model):
    import json

    from website.models.models import Team
    from website.models.race import Category, Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, _team = _make_team_in_race(django_user_model, slug="track-other-race")
    # a team that lives in a *different* race
    other_race = Race.objects.create(
        name="Other", slug="track-other", is_published=True
    )
    other_cat = Category.objects.create(code="open", name="Open", race=other_race)
    user = django_user_model.objects.create_user(
        username="other-owner", email="other-owner@example.com", password="x"
    )
    other_team = Team.objects.create(
        owner=user, category2=other_cat, teamname="Outsider"
    )

    path = _track_path(race.id)
    body = json.dumps(
        {"team_id": other_team.id, "points": [_valid_track_point()]}
    ).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 404


@pytest.mark.django_db
def test_track_upload_unpublished_race_returns_404(client, settings, django_user_model):
    import json

    from website.models.models import Team
    from website.models.race import Category, Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Hidden", slug="track-hidden", is_published=False)
    category = Category.objects.create(code="open", name="Open", race=race)
    user = django_user_model.objects.create_user(
        username="hidden-owner", email="hidden-owner@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Ghost")

    path = _track_path(race.id)
    body = json.dumps({"team_id": team.id, "points": [_valid_track_point()]}).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 404


@pytest.mark.django_db
def test_track_upload_nonexistent_race_returns_404(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    path = _track_path(999999)
    body = json.dumps({"team_id": 1, "points": [_valid_track_point()]}).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 404


@pytest.mark.django_db
def test_track_upload_malformed_point_returns_400(client, settings, django_user_model):
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-bad")
    path = _track_path(race.id)

    bad_points = [
        _valid_track_point(lat=91.0),  # out of range
        _valid_track_point(accuracy=-1.0),  # negative magnitude
    ]
    for bad in bad_points:
        body = json.dumps({"team_id": team.id, "points": [bad]}).encode()
        response = _signed_post(client, path, SECRET, body)
        assert response.status_code == 400

    # a missing required field
    missing = _valid_track_point()
    missing.pop("lat")
    body = json.dumps({"team_id": team.id, "points": [missing]}).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 400

    # nothing got written on a rejected batch
    assert TrackPoint.objects.count() == 0


@pytest.mark.django_db
def test_track_upload_mixed_valid_invalid_batch_all_or_nothing(
    client, settings, django_user_model
):
    """All-or-nothing: a batch with one valid and one invalid point writes nothing."""
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-mixed")
    path = _track_path(race.id)
    body = json.dumps(
        {
            "team_id": team.id,
            "points": [
                _valid_track_point(id="pt-good"),
                _valid_track_point(id="pt-bad", lat=91.0),  # lat out of range
            ],
        }
    ).encode()

    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 400
    assert TrackPoint.objects.count() == 0


@pytest.mark.django_db
def test_track_upload_over_500_points_returns_400(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-big")
    path = _track_path(race.id)
    points = [_valid_track_point(id=f"pt-{i}") for i in range(501)]
    body = json.dumps({"team_id": team.id, "points": points}).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 400


@pytest.mark.django_db
def test_track_upload_empty_points_acks_empty(client, settings, django_user_model):
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-empty")
    path = _track_path(race.id)
    body = json.dumps({"team_id": team.id, "points": []}).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 200
    assert response.json() == {"accepted": []}
    assert TrackPoint.objects.count() == 0


@pytest.mark.django_db
def test_track_upload_nullable_round_trip(client, settings, django_user_model):
    import json

    from apps.mobile.models import TrackPoint

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="track-null")
    path = _track_path(race.id)

    point = _valid_track_point(id="pt-null")
    for field in ("altitude", "vertical_accuracy", "trusted_ms", "boot_count"):
        point.pop(field)
    body = json.dumps({"team_id": team.id, "points": [point]}).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 200

    row = TrackPoint.objects.get(pk="pt-null")
    assert row.altitude is None
    assert row.vertical_accuracy is None
    assert row.trusted_ms is None
    assert row.boot_count is None


# --- Mark / MarkPresent models (Task 1) ------------------------------------


def _make_mark(team, race, **overrides):
    """Build (unsaved) a fully-populated Mark for model tests."""
    from apps.mobile.models import Mark

    fields = {
        "id": "mark-1",
        "team": team,
        "race": race,
        "source_install_id": "install-abc",
        "checkpoint_id": 264,
        "method": "nfc",
        "cp_code": "9f1a2b3c",
        "cp_nfc_uid": "04A2B3C4D5E680",
        "expected_count": 4,
        "complete": True,
        "verified": True,
        "trusted_ms": 1718900000123,
        "wall_ms": 1718900000000,
        "elapsed_at": 9876543,
        "boot_count": 7,
        "loc_lat": 55.75,
        "loc_lon": 37.61,
        "loc_accuracy": 6.5,
        "loc_altitude": 184.2,
        "loc_vertical_accuracy": 3.2,
        "loc_gps_time_ms": 1718899999000,
        "loc_elapsed_at": 9876100,
    }
    fields.update(overrides)
    return Mark(**fields)


@pytest.mark.django_db
def test_mark_round_trips_all_fields(django_user_model):
    from apps.mobile.models import Mark

    race, team = _make_team_in_race(django_user_model, slug="mark-race-1")
    _make_mark(team, race).save()

    m = Mark.objects.get(pk="mark-1")
    assert m.team_id == team.id
    assert m.race_id == race.id
    assert m.source_install_id == "install-abc"
    assert m.checkpoint_id == 264
    assert m.method == "nfc"
    assert m.cp_code == "9f1a2b3c"
    assert m.cp_nfc_uid == "04A2B3C4D5E680"
    assert m.expected_count == 4
    assert m.complete is True
    assert m.verified is True
    assert m.trusted_ms == 1718900000123
    assert m.wall_ms == 1718900000000
    assert m.elapsed_at == 9876543
    assert m.boot_count == 7
    assert m.loc_lat == 55.75
    assert m.loc_lon == 37.61
    assert m.loc_accuracy == 6.5
    assert m.loc_altitude == 184.2
    assert m.loc_vertical_accuracy == 3.2
    assert m.loc_gps_time_ms == 1718899999000
    assert m.loc_elapsed_at == 9876100
    assert m.created_at is not None


@pytest.mark.django_db
def test_mark_round_trips_nulls(django_user_model):
    from apps.mobile.models import Mark

    race, team = _make_team_in_race(django_user_model, slug="mark-race-2")
    _make_mark(
        team,
        race,
        id="mark-nulls",
        cp_code="",
        cp_nfc_uid="",
        trusted_ms=None,
        elapsed_at=None,
        boot_count=None,
        loc_lat=None,
        loc_lon=None,
        loc_accuracy=None,
        loc_altitude=None,
        loc_vertical_accuracy=None,
        loc_gps_time_ms=None,
        loc_elapsed_at=None,
    ).save()

    m = Mark.objects.get(pk="mark-nulls")
    assert m.cp_code == ""
    assert m.cp_nfc_uid == ""
    assert m.trusted_ms is None
    assert m.elapsed_at is None
    assert m.boot_count is None
    assert m.loc_lat is None
    assert m.loc_elapsed_at is None
    # wall_ms is required (sole fallback) and stays set
    assert m.wall_ms == 1718900000000


@pytest.mark.django_db
def test_markpresent_round_trips_and_sentinel(django_user_model):
    from apps.mobile.models import MarkPresent

    race, team = _make_team_in_race(django_user_model, slug="mark-race-3")
    mark = _make_mark(team, race, id="mark-present")
    mark.save()

    real = MarkPresent.objects.create(
        mark=mark,
        nfc_uid="04F1E2D3C4B5A6",
        code="c3d4",
        number=101,
        number_in_team=1,
    )
    sentinel = MarkPresent.objects.create(
        mark=mark,
        nfc_uid=None,
        code=None,
        number=0,
        number_in_team=2,
    )

    assert real.nfc_uid == "04F1E2D3C4B5A6"
    assert real.code == "c3d4"
    assert real.number == 101
    sentinel.refresh_from_db()
    assert sentinel.nfc_uid is None
    assert sentinel.code is None
    assert sentinel.number == 0
    assert mark.present.count() == 2


@pytest.mark.django_db
def test_mark_bulk_create_update_conflicts_upserts_and_preserves_created_at(
    django_user_model,
):
    from apps.mobile.models import MARK_UPDATE_FIELDS, Mark

    race, team = _make_team_in_race(django_user_model, slug="mark-race-4")

    # first insert: no GPS, incomplete
    first = _make_mark(
        team,
        race,
        id="mark-upsert",
        complete=False,
        verified=False,
        loc_lat=None,
        loc_lon=None,
        loc_accuracy=None,
        loc_altitude=None,
        loc_vertical_accuracy=None,
        loc_gps_time_ms=None,
        loc_elapsed_at=None,
    )
    Mark.objects.bulk_create(
        [first],
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=MARK_UPDATE_FIELDS,
    )
    assert Mark.objects.count() == 1
    original_created_at = Mark.objects.get(pk="mark-upsert").created_at

    # second send: same id, enriched payload (GPS + complete)
    enriched = _make_mark(
        team,
        race,
        id="mark-upsert",
        complete=True,
        verified=True,
        loc_lat=55.75,
        loc_lon=37.61,
    )
    Mark.objects.bulk_create(
        [enriched],
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=MARK_UPDATE_FIELDS,
    )

    assert Mark.objects.count() == 1
    m = Mark.objects.get(pk="mark-upsert")
    assert m.complete is True
    assert m.verified is True
    assert m.loc_lat == 55.75
    assert m.loc_lon == 37.61
    # created_at preserved (excluded from update_fields)
    assert m.created_at == original_created_at


@pytest.mark.django_db
def test_markpresent_unique_together_and_ignore_conflicts(django_user_model):
    from apps.mobile.models import MarkPresent

    race, team = _make_team_in_race(django_user_model, slug="mark-race-5")
    mark = _make_mark(team, race, id="mark-uniq")
    mark.save()

    MarkPresent.objects.create(
        mark=mark, nfc_uid="aa", code=None, number=1, number_in_team=1
    )

    # a duplicate (mark, number_in_team) is rejected
    with pytest.raises(IntegrityError):
        MarkPresent.objects.create(
            mark=mark, nfc_uid="bb", code=None, number=2, number_in_team=1
        )


@pytest.mark.django_db
def test_markpresent_bulk_create_ignore_conflicts_is_additive(django_user_model):
    from apps.mobile.models import MarkPresent

    race, team = _make_team_in_race(django_user_model, slug="mark-race-6")
    mark = _make_mark(team, race, id="mark-additive")
    mark.save()

    MarkPresent.objects.bulk_create(
        [MarkPresent(mark=mark, nfc_uid="aa", number=1, number_in_team=1)],
        ignore_conflicts=True,
    )
    assert mark.present.count() == 1

    # re-send slot 1 (collapsed) + a new slot 2 (inserted)
    MarkPresent.objects.bulk_create(
        [
            MarkPresent(mark=mark, nfc_uid="zz", number=9, number_in_team=1),
            MarkPresent(mark=mark, nfc_uid="bb", number=2, number_in_team=2),
        ],
        ignore_conflicts=True,
    )
    assert mark.present.count() == 2
    # existing slot 1 untouched
    assert mark.present.get(number_in_team=1).nfc_uid == "aa"


@pytest.mark.django_db
def test_markphoto_model_persists(django_user_model, settings, tmp_path):
    from django.core.files.base import ContentFile

    from apps.mobile.models import MarkPhoto

    settings.MEDIA_ROOT = str(tmp_path)
    race, team = _make_team_in_race(django_user_model, slug="mark-photo-race-1")
    mark = _make_mark(team, race, id="mark-photo-1", method="photo")
    mark.save()

    photo = MarkPhoto(mark=mark, frame_id="frame-1")
    photo.image.save("frame-1.jpg", ContentFile(b"fake-jpeg-bytes"), save=False)
    photo.save()

    stored = MarkPhoto.objects.get(mark=mark, frame_id="frame-1")
    assert stored.image.name == "mark_photos/mark-photo-1/frame-1.jpg"
    assert stored.created_at is not None


@pytest.mark.django_db
def test_markphoto_unique_together_raises_on_duplicate(
    django_user_model, settings, tmp_path
):
    from django.core.files.base import ContentFile

    from apps.mobile.models import MarkPhoto

    settings.MEDIA_ROOT = str(tmp_path)
    race, team = _make_team_in_race(django_user_model, slug="mark-photo-race-2")
    mark = _make_mark(team, race, id="mark-photo-2", method="photo")
    mark.save()

    def _photo():
        p = MarkPhoto(mark=mark, frame_id="frame-1")
        p.image.save("frame-1.jpg", ContentFile(b"bytes"), save=False)
        return p

    _photo().save()
    with pytest.raises(IntegrityError):
        _photo().save()


# --- Photo upload settings (mark-photo-upload plan, Task 2) ----------------


def test_data_upload_max_memory_size_raised_above_photo_cap():
    from django.conf import settings

    # Must exceed the view's PHOTO_MAX_BYTES (10 MB): SignedAppPermission reads
    # request.body before the view's own size check runs, so a body between the
    # Django default (2.5 MB) and the app cap must not be rejected by Django's
    # own RequestDataTooBig first. See settings.py for the full rationale.
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE == 12 * 1024 * 1024
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE > 10 * 1024 * 1024


def test_mobile_photo_throttle_rate_configured():
    from django.conf import settings

    assert (
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["mobile-photo"] == "120/min"
    )


# --- Mark upload serializers (Task 2) --------------------------------------


def _valid_present_member(**overrides):
    member = {
        "nfc_uid": "04F1E2D3C4B5A6",
        "code": "c3d4e5f6",
        "number": 101,
        "number_in_team": 1,
    }
    member.update(overrides)
    return member


def _valid_location(**overrides):
    loc = {
        "lat": 55.7501,
        "lon": 37.6109,
        "accuracy": 6.5,
        "altitude": 184.2,
        "vertical_accuracy": 3.2,
        "gps_time_ms": 1718899999000,
        "elapsed_at": 9876100,
    }
    loc.update(overrides)
    return loc


def _valid_mark(**overrides):
    mark = {
        "id": "0f9c1111-1111-1111-1111-111111111111",
        "checkpoint_id": 264,
        "method": "nfc",
        "cp_code": "9f1a2b3c4d5e6f70",
        "cp_nfc_uid": "04A2B3C4D5E680",
        "present": [_valid_present_member()],
        "expected_count": 4,
        "complete": True,
        "trusted_ms": 1718900000123,
        "wall_ms": 1718900000000,
        "elapsed_at": 9876543,
        "boot_count": 7,
        "location": _valid_location(),
    }
    mark.update(overrides)
    return mark


def _mark_upload_body(**overrides):
    body = {
        "team_id": 1234,
        "source_install_id": "b3c4-uuid",
        "marks": [_valid_mark()],
    }
    body.update(overrides)
    return body


def test_mark_upload_serializer_valid_batch():
    from apps.mobile.serializers import MarkUploadSerializer

    serializer = MarkUploadSerializer(data=_mark_upload_body())
    assert serializer.is_valid(), serializer.errors
    data = serializer.validated_data
    assert data["team_id"] == 1234
    assert data["source_install_id"] == "b3c4-uuid"
    assert len(data["marks"]) == 1
    mark = data["marks"][0]
    assert mark["id"] == "0f9c1111-1111-1111-1111-111111111111"
    assert mark["method"] == "nfc"
    assert mark["location"]["lat"] == 55.7501
    assert len(mark["present"]) == 1
    assert mark["present"][0]["number_in_team"] == 1


def test_mark_serializer_omitted_nullables_resolve_absent():
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark()
    for field in ("trusted_ms", "elapsed_at", "boot_count", "location"):
        mark.pop(field)
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["marks"][0]
    assert "trusted_ms" not in parsed
    assert "elapsed_at" not in parsed
    assert "boot_count" not in parsed
    assert "location" not in parsed


def test_mark_serializer_explicit_null_nullables():
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark(trusted_ms=None, elapsed_at=None, boot_count=None, location=None)
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["marks"][0]
    assert parsed["trusted_ms"] is None
    assert parsed["elapsed_at"] is None
    assert parsed["boot_count"] is None
    assert parsed["location"] is None


def test_mark_serializer_location_null_accepted():
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark(location=None)
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["marks"][0]["location"] is None


def test_mark_serializer_empty_present_accepted():
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark(present=[])
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["marks"][0]["present"] == []


def test_mark_serializer_over_100_present_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    members = [_valid_present_member(number_in_team=i) for i in range(101)]
    mark = _valid_mark(present=members)
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert not serializer.is_valid()


def test_mark_serializer_present_sentinel_accepted():
    from apps.mobile.serializers import MarkUploadSerializer

    member = _valid_present_member(nfc_uid=None, code=None, number=0)
    mark = _valid_mark(present=[member])
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["marks"][0]["present"][0]
    assert parsed["nfc_uid"] is None
    assert parsed["code"] is None
    assert parsed["number"] == 0


def test_mark_serializer_blank_cp_fields_accepted():
    """A future photo mark carries no scanned КП code/uid."""
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark(method="photo", cp_code="", cp_nfc_uid="")
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["marks"][0]
    assert parsed["cp_code"] == ""
    assert parsed["cp_nfc_uid"] == ""


def test_mark_serializer_missing_required_field_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    for field in (
        "id",
        "wall_ms",
        "checkpoint_id",
        "method",
        "cp_code",
        "cp_nfc_uid",
        "expected_count",
        "complete",
        "present",
    ):
        mark = _valid_mark()
        mark.pop(field)
        serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
        assert not serializer.is_valid(), f"missing {field} should be invalid"
        assert "marks" in serializer.errors


def test_mark_serializer_out_of_range_location_lat_lon_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    for field, bad in [
        ("lat", 91.0),
        ("lat", -91.0),
        ("lon", 181.0),
        ("lon", -181.0),
        ("accuracy", -0.1),
        ("vertical_accuracy", -0.1),
    ]:
        mark = _valid_mark(location=_valid_location(**{field: bad}))
        serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
        assert not serializer.is_valid(), f"location {field}={bad} should be invalid"


def test_mark_serializer_nan_inf_location_floats_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    float_fields = ["lat", "lon", "accuracy", "altitude", "vertical_accuracy"]
    for field in float_fields:
        for bad in ["NaN", "1e309", "-1e309", "Inf"]:
            mark = _valid_mark(location=_valid_location(**{field: bad}))
            serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
            assert (
                not serializer.is_valid()
            ), f"{bad!r} in location {field!r} should be invalid"


def test_mark_serializer_bad_method_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark(method="qrcode")
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert not serializer.is_valid()
    assert "marks" in serializer.errors


def test_mark_serializer_empty_id_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    mark = _valid_mark(id="")
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert not serializer.is_valid()


def test_mark_upload_serializer_over_500_marks_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    marks = [_valid_mark(id=f"mark-{i}") for i in range(501)]
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=marks))
    assert not serializer.is_valid()
    assert "marks" in serializer.errors


def test_mark_upload_serializer_empty_marks_valid():
    from apps.mobile.serializers import MarkUploadSerializer

    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[]))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["marks"] == []


def test_mark_upload_serializer_missing_source_install_id_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    body = _mark_upload_body()
    body.pop("source_install_id")
    serializer = MarkUploadSerializer(data=body)
    assert not serializer.is_valid()
    assert "source_install_id" in serializer.errors


def test_mark_serializer_oversized_32bit_ints_invalid():
    """32-bit IntegerField columns must 400, not 500, on an oversized int."""
    from apps.mobile.serializers import MarkUploadSerializer

    too_big = 2147483648  # 2^31, one past the 32-bit signed max
    for field in ("checkpoint_id", "expected_count", "boot_count"):
        mark = _valid_mark(**{field: too_big})
        serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
        assert not serializer.is_valid(), f"oversized {field} should be invalid"

    for field in ("number", "number_in_team"):
        member = _valid_present_member(**{field: too_big})
        mark = _valid_mark(present=[member])
        serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
        assert not serializer.is_valid(), f"oversized present.{field} should be invalid"


def test_mark_serializer_bigint_fields_accept_over_32bit():
    """BigInt columns accept a 2^31..2^63 value (wider cap than the 32-bit ones)."""
    from apps.mobile.serializers import MarkUploadSerializer

    big = 2147483648  # past 32-bit max, well within BigInt
    mark = _valid_mark(trusted_ms=big, wall_ms=big, elapsed_at=big)
    mark["location"] = _valid_location(gps_time_ms=big, elapsed_at=big)
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert serializer.is_valid(), serializer.errors
    parsed = serializer.validated_data["marks"][0]
    assert parsed["trusted_ms"] == big
    assert parsed["wall_ms"] == big
    assert parsed["elapsed_at"] == big


def test_mark_serializer_oversized_bigint_invalid():
    """A 2^63 value still 400s on the BigInt fields (DataError guard)."""
    from apps.mobile.serializers import MarkUploadSerializer

    too_big = 9223372036854775808  # 2^63, one past the BigInt signed max
    for field in ("trusted_ms", "wall_ms", "elapsed_at"):
        mark = _valid_mark(**{field: too_big})
        serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
        assert not serializer.is_valid(), f"oversized {field} should be invalid"

    for loc_field in ("gps_time_ms", "elapsed_at"):
        mark = _valid_mark(location=_valid_location(**{loc_field: too_big}))
        serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
        assert (
            not serializer.is_valid()
        ), f"oversized location.{loc_field} should be invalid"


def test_mark_serializer_oversized_present_strings_invalid():
    from apps.mobile.serializers import MarkUploadSerializer

    member = _valid_present_member(nfc_uid="A" * 256)
    mark = _valid_mark(present=[member])
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert not serializer.is_valid(), "oversized nfc_uid should be invalid"

    member = _valid_present_member(code="B" * 65)
    mark = _valid_mark(present=[member])
    serializer = MarkUploadSerializer(data=_mark_upload_body(marks=[mark]))
    assert not serializer.is_valid(), "oversized code should be invalid"


# --- Mark upload endpoint (Task 3) -----------------------------------------


def _marks_path(race_id):
    return f"/app/race/{race_id}/marks/"


def _make_cp_with_tag(race, number=1):
    """Create a КП + a CheckpointTag whose signals populate code/bid.

    Returns ``(checkpoint, cp_code_hex)`` where ``cp_code_hex`` is the wire
    ``cp_code`` (hex of the tag's raw 16-byte code) that verifies against the
    tag's ``bid`` — i.e. ``sha256(bytes.fromhex(cp_code))[:16] == tag.bid``.
    """
    from website.models.checkpoint import Checkpoint, CheckpointTag

    cp = Checkpoint.objects.create(race=race, number=number, cost=3, description="tree")
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid=f"04AABBCC{number:02X}")
    tag.refresh_from_db()
    return cp, bytes(tag.code).hex()


@pytest.mark.django_db
def test_mark_upload_happy_path_persists_and_acks(client, settings, django_user_model):
    import json

    from apps.mobile.models import Mark, MarkPresent

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-happy")
    cp, cp_code = _make_cp_with_tag(race)
    path = _marks_path(race.id)
    mark = _valid_mark(id="mk-a", checkpoint_id=cp.id, cp_code=cp_code)
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "phone-1", "marks": [mark]}
    ).encode()

    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 200
    assert response.json() == {"accepted": ["mk-a"]}

    assert Mark.objects.count() == 1
    row = Mark.objects.get(pk="mk-a")
    assert row.race_id == race.id
    assert row.team_id == team.id
    # source_install_id comes from the signed BODY, not the X-Install-Id header.
    assert row.source_install_id == "phone-1"
    assert row.checkpoint_id == cp.id
    assert row.verified is True
    assert row.loc_lat == 55.7501
    assert MarkPresent.objects.filter(mark=row).count() == 1


@pytest.mark.django_db
def test_mark_upload_idempotent_no_duplicate_rows(client, settings, django_user_model):
    import json

    from apps.mobile.models import Mark, MarkPresent

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-idem")
    cp, cp_code = _make_cp_with_tag(race)
    path = _marks_path(race.id)
    mark = _valid_mark(id="mk-x", checkpoint_id=cp.id, cp_code=cp_code)
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()

    r1 = _signed_post(client, path, SECRET, body)
    r2 = _signed_post(client, path, SECRET, body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json() == {"accepted": ["mk-x"]}
    assert Mark.objects.count() == 1
    assert MarkPresent.objects.count() == 1


@pytest.mark.django_db
def test_mark_upload_enrichment_merge_late_gps_and_member(
    client, settings, django_user_model
):
    """A repeat id enriches (GPS + grown roster + complete) without losing data."""
    import json

    from apps.mobile.models import Mark, MarkPresent

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-enrich")
    cp, cp_code = _make_cp_with_tag(race)
    path = _marks_path(race.id)

    # First POST: no GPS fix yet, partial roster, not complete.
    first = _valid_mark(
        id="mk-e",
        checkpoint_id=cp.id,
        cp_code=cp_code,
        complete=False,
        location=None,
        present=[_valid_present_member(number_in_team=1)],
    )
    body1 = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [first]}
    ).encode()
    r1 = _signed_post(client, path, SECRET, body1)
    assert r1.status_code == 200

    row = Mark.objects.get(pk="mk-e")
    created_at = row.created_at
    assert row.loc_lat is None
    assert row.complete is False
    assert MarkPresent.objects.filter(mark=row).count() == 1

    # Second POST: same id, now carrying the GPS fix + a second member + complete.
    second = _valid_mark(
        id="mk-e",
        checkpoint_id=cp.id,
        cp_code=cp_code,
        complete=True,
        location=_valid_location(),
        present=[
            _valid_present_member(number_in_team=1),
            _valid_present_member(nfc_uid="04DEAD01", number=202, number_in_team=2),
        ],
    )
    body2 = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [second]}
    ).encode()
    r2 = _signed_post(client, path, SECRET, body2)
    assert r2.status_code == 200

    assert Mark.objects.count() == 1  # no duplicate row
    row.refresh_from_db()
    assert row.loc_lat == 55.7501  # enriched
    assert row.complete is True  # false -> true
    assert row.created_at == created_at  # insert time preserved
    members = MarkPresent.objects.filter(mark=row).order_by("number_in_team")
    assert [m.number_in_team for m in members] == [1, 2]  # added slot, kept old


@pytest.mark.django_db
def test_mark_upload_verified_matrix(client, settings, django_user_model):
    """good code -> True; bad hex / unknown cp / mismatch -> False; all stored."""
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-verify")
    cp, cp_code = _make_cp_with_tag(race)
    path = _marks_path(race.id)

    cases = [
        # (id, checkpoint_id, cp_code, expected_verified)
        ("mk-good", cp.id, cp_code, True),
        ("mk-badhex", cp.id, "ZZZZ", False),  # non-hex
        ("mk-blank", cp.id, "", False),  # blank cp_code (future photo)
        ("mk-unknown", 999999, cp_code, False),  # unknown КП
        ("mk-mismatch", cp.id, "00112233", False),  # valid hex, wrong code
    ]
    for mid, cp_id, code, _ in cases:
        mark = _valid_mark(id=mid, checkpoint_id=cp_id, cp_code=code)
        body = json.dumps(
            {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
        ).encode()
        assert _signed_post(client, path, SECRET, body).status_code == 200

    for mid, _cp_id, _code, expected in cases:
        assert Mark.objects.get(pk=mid).verified is expected


@pytest.mark.django_db
def test_mark_upload_null_location_and_times_stored(
    client, settings, django_user_model
):
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-null")
    path = _marks_path(race.id)
    mark = _valid_mark(id="mk-null", checkpoint_id=1, location=None)
    for field in ("trusted_ms", "elapsed_at", "boot_count"):
        mark.pop(field)
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 200

    row = Mark.objects.get(pk="mk-null")
    assert row.loc_lat is None
    assert row.loc_gps_time_ms is None
    assert row.trusted_ms is None
    assert row.elapsed_at is None
    assert row.boot_count is None


@pytest.mark.django_db
def test_mark_upload_present_sentinel_row_stored(client, settings, django_user_model):
    import json

    from apps.mobile.models import MarkPresent

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-sentinel")
    path = _marks_path(race.id)
    sentinel = _valid_present_member(nfc_uid=None, code=None, number=0)
    mark = _valid_mark(id="mk-sent", checkpoint_id=1, present=[sentinel])
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 200

    row = MarkPresent.objects.get(mark_id="mk-sent")
    assert row.nfc_uid is None
    assert row.number == 0


@pytest.mark.django_db
def test_mark_upload_blank_cp_code_stored_unverified(
    client, settings, django_user_model
):
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-photo")
    path = _marks_path(race.id)
    mark = _valid_mark(
        id="mk-photo", checkpoint_id=1, method="photo", cp_code="", cp_nfc_uid=""
    )
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 200

    row = Mark.objects.get(pk="mk-photo")
    assert row.cp_code == ""
    assert row.verified is False


@pytest.mark.django_db
def test_mark_upload_in_batch_duplicate_id(client, settings, django_user_model):
    """Same id twice in one batch -> 200, one row with last data, no 500."""
    import json

    from apps.mobile.models import Mark, MarkPresent

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-dupid")
    path = _marks_path(race.id)
    m1 = _valid_mark(id="mk-dup", checkpoint_id=1, complete=False, location=None)
    m2 = _valid_mark(
        id="mk-dup", checkpoint_id=1, complete=True, location=_valid_location()
    )
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [m1, m2]}
    ).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 200
    # accepted echoes every submitted id (incl. the in-batch duplicate).
    assert response.json() == {"accepted": ["mk-dup", "mk-dup"]}

    assert Mark.objects.count() == 1
    row = Mark.objects.get(pk="mk-dup")
    assert row.complete is True  # last occurrence wins
    assert row.loc_lat == 55.7501
    # Only one MarkPresent row — the de-dup keeps last occurrence's present[].
    assert MarkPresent.objects.filter(mark_id="mk-dup").count() == 1


@pytest.mark.django_db
def test_mark_upload_team_not_in_race_returns_404(client, settings, django_user_model):
    import json

    from website.models.models import Team
    from website.models.race import Category, Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, _team = _make_team_in_race(django_user_model, slug="marks-other-race")
    other_race = Race.objects.create(
        name="Other", slug="marks-other", is_published=True
    )
    other_cat = Category.objects.create(code="open", name="Open", race=other_race)
    user = django_user_model.objects.create_user(
        username="m-other-owner", email="m-other-owner@example.com", password="x"
    )
    other_team = Team.objects.create(
        owner=user, category2=other_cat, teamname="Outsider"
    )

    path = _marks_path(race.id)
    body = json.dumps(
        {
            "team_id": other_team.id,
            "source_install_id": "ph",
            "marks": [_valid_mark(checkpoint_id=1)],
        }
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 404


@pytest.mark.django_db
def test_mark_upload_unpublished_race_returns_404(client, settings, django_user_model):
    import json

    from website.models.models import Team
    from website.models.race import Category, Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Hidden", slug="marks-hidden", is_published=False)
    category = Category.objects.create(code="open", name="Open", race=race)
    user = django_user_model.objects.create_user(
        username="m-hidden-owner", email="m-hidden-owner@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Ghost")

    path = _marks_path(race.id)
    body = json.dumps(
        {
            "team_id": team.id,
            "source_install_id": "ph",
            "marks": [_valid_mark(checkpoint_id=1)],
        }
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 404


@pytest.mark.django_db
def test_mark_upload_nonexistent_race_returns_404(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    path = _marks_path(999999)
    body = json.dumps(
        {
            "team_id": 1,
            "source_install_id": "ph",
            "marks": [_valid_mark(checkpoint_id=1)],
        }
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 404


@pytest.mark.django_db
def test_mark_upload_wrong_signature_returns_403(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-403")
    path = _marks_path(race.id)
    body = json.dumps(
        {
            "team_id": team.id,
            "source_install_id": "ph",
            "marks": [_valid_mark(checkpoint_id=1)],
        }
    ).encode()
    response = _signed_post(client, path, "wrong-secret", body)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_mark_upload_over_500_marks_returns_400(client, settings, django_user_model):
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-big")
    path = _marks_path(race.id)
    marks = [_valid_mark(id=f"mk-{i}", checkpoint_id=1) for i in range(501)]
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": marks}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 400
    assert Mark.objects.count() == 0


@pytest.mark.django_db
def test_mark_upload_mixed_valid_invalid_batch_all_or_nothing(
    client, settings, django_user_model
):
    """A bad mark anywhere in the batch -> 400, zero rows written (all-or-nothing)."""
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-mixed")
    path = _marks_path(race.id)
    marks = [
        _valid_mark(id="mk-good", checkpoint_id=1),
        _valid_mark(id="mk-bad", checkpoint_id=1, location=_valid_location(lat=91.0)),
    ]
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": marks}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 400
    assert Mark.objects.count() == 0


@pytest.mark.django_db
def test_mark_upload_oversized_int_returns_400_not_500(
    client, settings, django_user_model
):
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-overint")
    path = _marks_path(race.id)
    too_big = 2147483648  # 2^31, one past the 32-bit signed max
    for field in ("checkpoint_id", "expected_count", "boot_count"):
        mark = _valid_mark(**{field: too_big})
        body = json.dumps(
            {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
        ).encode()
        assert _signed_post(client, path, SECRET, body).status_code == 400

    member = _valid_present_member(number=too_big)
    mark = _valid_mark(checkpoint_id=1, present=[member])
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 400

    member = _valid_present_member(number_in_team=too_big)
    mark = _valid_mark(checkpoint_id=1, present=[member])
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 400

    assert Mark.objects.count() == 0


@pytest.mark.django_db
def test_mark_upload_invalid_method_returns_400(client, settings, django_user_model):
    import json

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-method")
    path = _marks_path(race.id)
    mark = _valid_mark(checkpoint_id=1, method="qr")
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()
    assert _signed_post(client, path, SECRET, body).status_code == 400


@pytest.mark.django_db
def test_mark_upload_empty_batch_acks_empty(client, settings, django_user_model):
    import json

    from apps.mobile.models import Mark

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-empty")
    path = _marks_path(race.id)
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": []}
    ).encode()
    response = _signed_post(client, path, SECRET, body)
    assert response.status_code == 200
    assert response.json() == {"accepted": []}
    assert Mark.objects.count() == 0


@pytest.mark.django_db
def test_mark_upload_duplicate_number_in_team_collapsed_on_resend(
    client, settings, django_user_model
):
    """A re-sent present slot collapses via unique_together (additive insert)."""
    import json

    from apps.mobile.models import MarkPresent

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-collapse")
    path = _marks_path(race.id)
    mark = _valid_mark(
        id="mk-c", checkpoint_id=1, present=[_valid_present_member(number_in_team=1)]
    )
    body = json.dumps(
        {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
    ).encode()

    _signed_post(client, path, SECRET, body)
    _signed_post(client, path, SECRET, body)  # same slot again
    assert MarkPresent.objects.filter(mark_id="mk-c", number_in_team=1).count() == 1


@pytest.mark.django_db
def test_mark_upload_throttle_returns_429_after_limit(
    client, settings, django_user_model
):
    """``mobile-write`` ScopedRateThrottle fires 429 when the rate is exceeded."""
    import json

    from rest_framework.throttling import SimpleRateThrottle

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race, team = _make_team_in_race(django_user_model, slug="marks-throttle")
    path = _marks_path(race.id)

    original_rates = SimpleRateThrottle.THROTTLE_RATES
    SimpleRateThrottle.THROTTLE_RATES = {**original_rates, "mobile-write": "2/min"}
    try:
        statuses = []
        for i in range(4):
            mark = _valid_mark(id=f"mk-t{i}", checkpoint_id=1)
            body = json.dumps(
                {"team_id": team.id, "source_install_id": "ph", "marks": [mark]}
            ).encode()
            statuses.append(_signed_post(client, path, SECRET, body).status_code)
    finally:
        SimpleRateThrottle.THROTTLE_RATES = original_rates

    assert 429 in statuses[2:]


# --- Mark photo upload endpoint (Task 3) ------------------------------------


def _photo_path(race_id, mark_id, frame_id):
    return f"/app/race/{race_id}/mark/{mark_id}/photo/{frame_id}"


def _signed_photo_post(client, path, secret, body_bytes, key_id="test-v1"):
    """POST a raw JPEG body with a build-HMAC signature over the raw bytes.

    Distinct from ``_signed_post``: sends ``Content-Type: image/jpeg`` so the
    binary path (never ``request.data``) is genuinely exercised.
    """
    headers = _signed_headers("POST", path, secret, body=body_bytes, key_id=key_id)
    return client.post(path, data=body_bytes, content_type="image/jpeg", **headers)


def _make_photo_mark(team, race, mark_id="mk-photo-1"):
    mark = _make_mark(team, race, id=mark_id, method="photo", cp_code="", cp_nfc_uid="")
    mark.save()
    return mark


@pytest.mark.django_db
def test_mark_photo_upload_happy_path_creates_row_and_file(
    client, settings, django_user_model, tmp_path
):
    from apps.mobile.models import MarkPhoto

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-happy")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")
    body = b"\xff\xd8fake-jpeg-bytes"

    response = _signed_photo_post(client, path, SECRET, body)
    assert response.status_code == 201

    photo = MarkPhoto.objects.get(mark=mark, frame_id="frame-1")
    assert photo.image.name == f"mark_photos/{mark.id}/frame-1.jpg"
    with photo.image.open("rb") as f:
        assert f.read() == body


@pytest.mark.django_db
def test_mark_photo_upload_idempotent_resend_no_duplicate(
    client, settings, django_user_model, tmp_path
):
    from apps.mobile.models import MarkPhoto

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-idem")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")
    body = b"\xff\xd8fake-jpeg-bytes"

    first = _signed_photo_post(client, path, SECRET, body)
    second = _signed_photo_post(client, path, SECRET, body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert MarkPhoto.objects.filter(mark=mark, frame_id="frame-1").count() == 1


@pytest.mark.django_db
def test_mark_photo_upload_unpublished_race_returns_404(
    client, settings, django_user_model, tmp_path
):
    from website.models.models import Team
    from website.models.race import Category, Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race = Race.objects.create(name="Unpub", slug="photo-unpub", is_published=False)
    category = Category.objects.create(code="open", name="Open", race=race)
    user = django_user_model.objects.create_user(
        username="owner-photo-unpub", email="photo-unpub@example.com", password="x"
    )
    team = Team.objects.create(owner=user, category2=category, teamname="Alpha")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")

    response = _signed_photo_post(client, path, SECRET, b"\xff\xd8bytes")
    assert response.status_code == 404


@pytest.mark.django_db
def test_mark_photo_upload_unknown_race_returns_404(
    client, settings, django_user_model, tmp_path
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    path = _photo_path(999999, "mk-none", "frame-1")
    response = _signed_photo_post(client, path, SECRET, b"\xff\xd8bytes")
    assert response.status_code == 404


@pytest.mark.django_db
def test_mark_photo_upload_mark_not_arrived_returns_404(
    client, settings, django_user_model, tmp_path
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-no-mark")
    path = _photo_path(race.id, "mk-never-uploaded", "frame-1")
    response = _signed_photo_post(client, path, SECRET, b"\xff\xd8bytes")
    assert response.status_code == 404


@pytest.mark.django_db
def test_mark_photo_upload_mark_belongs_to_other_race_returns_404(
    client, settings, django_user_model, tmp_path
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race_a, team_a = _make_team_in_race(django_user_model, slug="photo-race-a")
    race_b, _team_b = _make_team_in_race(django_user_model, slug="photo-race-b")
    mark = _make_photo_mark(team_a, race_a)

    path = _photo_path(race_b.id, mark.id, "frame-1")
    response = _signed_photo_post(client, path, SECRET, b"\xff\xd8bytes")
    assert response.status_code == 404


@pytest.mark.django_db
def test_mark_photo_upload_empty_body_returns_400(
    client, settings, django_user_model, tmp_path
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-empty")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")

    response = _signed_photo_post(client, path, SECRET, b"")
    assert response.status_code == 400


@pytest.mark.django_db
def test_mark_photo_upload_wrong_signature_returns_403(
    client, settings, django_user_model, tmp_path
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-403")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")

    response = _signed_photo_post(client, path, "wrong-secret", b"\xff\xd8bytes")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_mark_photo_upload_throttle_returns_429_after_limit(
    client, settings, django_user_model, tmp_path
):
    from rest_framework.throttling import SimpleRateThrottle

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-throttle")
    mark = _make_photo_mark(team, race)

    original_rates = SimpleRateThrottle.THROTTLE_RATES
    SimpleRateThrottle.THROTTLE_RATES = {**original_rates, "mobile-photo": "2/min"}
    try:
        statuses = []
        for i in range(4):
            path = _photo_path(race.id, mark.id, f"frame-{i}")
            statuses.append(
                _signed_photo_post(client, path, SECRET, b"\xff\xd8bytes").status_code
            )
    finally:
        SimpleRateThrottle.THROTTLE_RATES = original_rates

    assert 429 in statuses[2:]


@pytest.mark.django_db
@pytest.mark.parametrize("bad_frame_id", ["a.b", "a.jpg", "a~b"])
def test_mark_photo_upload_bad_charset_frame_id_returns_400(
    client, settings, django_user_model, tmp_path, bad_frame_id
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(
        django_user_model,
        slug=f"photo-bad-{bad_frame_id.replace('.', '_').replace('~', '_')}",
    )
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, bad_frame_id)

    response = _signed_photo_post(client, path, SECRET, b"\xff\xd8bytes")
    assert response.status_code == 400


@pytest.mark.django_db
def test_mark_photo_upload_unrouteable_frame_id_returns_url_level_404(
    client, settings, django_user_model, tmp_path
):
    """A frame_id that is empty or contains a slash never routes to the view
    at all -- the <str:frame_id> URL converter itself rejects it before
    resolving, so this is a plain Django 404, not the view's explicit 400."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-unrouteable")
    mark = _make_photo_mark(team, race)
    base = f"/app/race/{race.id}/mark/{mark.id}/photo/"

    for unrouteable_path in [base, base + "../x", base + "a/b"]:
        response = _signed_photo_post(
            client, unrouteable_path, SECRET, b"\xff\xd8bytes"
        )
        assert response.status_code == 404, unrouteable_path


@pytest.mark.django_db
def test_mark_photo_upload_orphan_canonical_file_reused_on_retry(
    client, settings, django_user_model, tmp_path
):
    """A canonical file left by a crashed prior attempt (write succeeded, row
    commit didn't) must be reused on retry -- not suffixed ``_XXXX`` by
    storage's ``get_available_name``."""
    from apps.mobile.models import MarkPhoto

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-orphan")
    mark = _make_photo_mark(team, race)
    canonical_rel = f"mark_photos/{mark.id}/frame-1.jpg"
    canonical_abs = tmp_path / canonical_rel
    canonical_abs.parent.mkdir(parents=True, exist_ok=True)
    canonical_abs.write_bytes(b"leftover-from-crash")

    path = _photo_path(race.id, mark.id, "frame-1")
    response = _signed_photo_post(client, path, SECRET, b"\xff\xd8fresh-bytes")
    assert response.status_code == 201

    photo = MarkPhoto.objects.get(mark=mark, frame_id="frame-1")
    assert photo.image.name == canonical_rel  # no "_XXXX" suffix
    with photo.image.open("rb") as f:
        assert f.read() == b"\xff\xd8fresh-bytes"


@pytest.mark.django_db
def test_mark_photo_upload_over_app_cap_under_django_cap_returns_413(
    client, settings, django_user_model, tmp_path
):
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

    race, team = _make_team_in_race(django_user_model, slug="photo-413")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")
    body = b"x" * (11 * 1024 * 1024)  # > PHOTO_MAX_BYTES (10MB), < Django cap (12MB)

    response = _signed_photo_post(client, path, SECRET, body)
    assert response.status_code == 413


@pytest.mark.django_db
def test_mark_photo_upload_over_django_cap_returns_400(
    client, settings, django_user_model, tmp_path
):
    """Above DATA_UPLOAD_MAX_MEMORY_SIZE, Django's own RequestDataTooBig fires
    inside SignedAppPermission (reading request.body) before the view's 413
    check ever runs. The contract treats 400/413 as equivalent -- this test
    just documents which one fires for a body this large."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

    race, team = _make_team_in_race(django_user_model, slug="photo-400-big")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")
    body = b"x" * (13 * 1024 * 1024)  # > Django cap (12MB)

    response = _signed_photo_post(client, path, SECRET, body)
    assert response.status_code == 400


@pytest.mark.django_db
def test_mark_photo_upload_no_accept_header_not_406(
    client, settings, django_user_model, tmp_path
):
    """Guards gotcha #4: DRF's response content negotiation runs before the
    view. Our bare-status responses have no body, so this only matters if the
    client sends an Accept header DRF's JSONRenderer can't satisfy -- the
    Android client sends none (or ``*/*``), never ``image/jpeg``."""
    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300
    settings.MEDIA_ROOT = str(tmp_path)

    race, team = _make_team_in_race(django_user_model, slug="photo-accept")
    mark = _make_photo_mark(team, race)
    path = _photo_path(race.id, mark.id, "frame-1")
    body = b"\xff\xd8bytes"

    headers = _signed_headers("POST", path, SECRET, body=body)
    headers["HTTP_ACCEPT"] = "*/*"
    response = client.post(path, data=body, content_type="image/jpeg", **headers)
    assert response.status_code != 406
