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
def test_legend_checkpoint_serializer_open_exposes_cleartext():
    from apps.mobile.serializers import LegendCheckpointSerializer
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Open ser", slug="open-ser")
    point = Checkpoint.objects.create(race=race, number=1, cost=4, description="tree")

    data = LegendCheckpointSerializer(point).data

    assert set(data.keys()) == {"id", "number", "type", "cost", "description"}
    assert data["cost"] == 4
    assert data["description"] == "tree"
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
        race=race, number=1, cost=4, description="tree", is_legend_locked=True
    )
    secret = seal_checkpoint(point)

    data = LegendCheckpointSerializer(point).data

    assert set(data.keys()) == {"id", "number", "type", "enc"}
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
    assert set(data.keys()) == {"id", "number", "type"}


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
    assert data["tags"] == []
    assert [c["number"] for c in data["checkpoints"]] == [1, 2, 3]
    first = data["checkpoints"][0]
    assert set(first.keys()) == {"id", "number", "cost", "type", "description"}
    assert first["type"] == "kp"
    assert first["description"] == "first"


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
def test_legend_excludes_draft_checkpoints(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Draft race", slug="draft-race")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    draft = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="draft cp", type="draft"
    )
    CheckpointTag.objects.create(point=draft, nfc_uid="DRAFT:TAG")

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
        race=race, number=1, cost=4, description="secret tree", is_legend_locked=True
    )
    Checkpoint.objects.create(race=race, number=2, cost=2, description="open spot")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    locked, open_cp = data["checkpoints"]

    assert set(locked.keys()) == {"id", "number", "type", "enc"}
    assert set(locked["enc"].keys()) == {"iv", "ct"}
    assert "cost" not in locked
    assert "description" not in locked

    assert set(open_cp.keys()) == {"id", "number", "type", "cost", "description"}
    assert open_cp["description"] == "open spot"

    # the locked КП's cleartext never appears anywhere in the serialized body
    body = response.content.decode()
    assert "secret tree" not in body
    # ...but the open КП's cleartext does
    assert "open spot" in body


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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")
    tag.refresh_from_db()
    code = bytes(tag.code)  # what is written into the physical NFC tag's memory

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))
    assert response.status_code == 200
    data = response.json()

    # 1. locate the tag by the bid computed from the scanned code; its `point`
    #    identifies which КП was physically scanned (always present)
    bid = hashlib.sha256(code).hexdigest()[:16]
    tag_entry = next(t for t in data["tags"] if t["bid"] == bid)
    assert tag_entry["check_method"] == "offline"
    assert tag_entry["point"] == cp.id

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
def test_legend_tags_include_open_checkpoint_tag_with_point_no_iv_ct(client, settings):
    """An open-КП tag rides in `tags` with `point` for identity but no iv/ct."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Open tag", slug="open-tag")
    cp = Checkpoint.objects.create(race=race, number=1, cost=2, description="open spot")
    tag = CheckpointTag.objects.create(
        point=cp, nfc_uid="04A1B2C3", check_method="offline"
    )
    tag.refresh_from_db()

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert len(data["tags"]) == 1
    entry = data["tags"][0]
    assert entry["point"] == cp.id
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
        [CheckpointTag(point=cp, nfc_uid="04A1B2C3", check_method="offline")]
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
        point=cp, nfc_uid="04A1B2C3", check_method="offline"
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
        point=cp, nfc_uid="04A1B2C3", check_method="offline"
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
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")

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
def test_legend_version_changes_when_kp_flips_to_draft():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Kp to draft", slug="kp-to-draft")
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    before = legend_version(race.id)
    cp.type = "draft"
    cp.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_draft_flips_to_kp():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Draft to kp", slug="draft-to-kp")
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="cp", type="draft"
    )
    before = legend_version(race.id)
    cp.type = "kp"
    cp.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_unchanged_when_draft_checkpoint_edited():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Draft edit", slug="draft-edit")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    draft = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="draft", type="draft"
    )
    before = legend_version(race.id)
    draft.description = "draft edited"
    draft.save()
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
        point=cp, nfc_uid="AA:BB", check_method="offline"
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
        point=cp, nfc_uid="AA:BB", check_method="offline"
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
        point=cp, nfc_uid="AA:BB", check_method="offline"
    )
    after_add = legend_version(race.id)
    assert before != after_add
    tag.delete()
    assert legend_version(race.id) != after_add


@pytest.mark.django_db
def test_legend_version_unchanged_when_tag_on_draft_checkpoint_added():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag draft", slug="tag-draft")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    draft = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="draft", type="draft"
    )
    before = legend_version(race.id)
    CheckpointTag.objects.create(point=draft, nfc_uid="AA:BB", check_method="offline")
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
    CheckpointTag.objects.create(point=cp, nfc_uid="AA:BB", check_method="offline")
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
    assert set(data["versions"].keys()) == {"teams", "legend"}
    assert data["versions"]["teams"]  # non-empty fingerprint
    assert data["versions"]["legend"]  # non-empty fingerprint


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
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")

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
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")

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
    tag = CheckpointTag.objects.create(point=p1, nfc_uid="DEADBEEF")

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
    tag = CheckpointTag(point=point, nfc_uid="CAFEBABE")  # unsaved

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
    tag = CheckpointTag(point=point, nfc_uid="04A1B2C3")

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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")

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
    tag = CheckpointTag.objects.create(point=locked, nfc_uid="04A1B2C3")
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
def test_build_bundle_excludes_cross_race_and_draft_unlocks():
    """Cross-race and draft КП in tag.unlocks must not contribute content keys.

    A tag in Race A whose unlocks M2M includes a locked КП from Race B or a
    draft КП must not expose those keys in the bundle.
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

    draft_locked = Checkpoint.objects.create(
        race=race_a,
        number=2,
        cost=3,
        description="draft",
        type="draft",
        is_legend_locked=True,
    )
    seal_checkpoint(draft_locked)

    tag = CheckpointTag.objects.create(point=local_locked, nfc_uid="04AABBCC")
    tag.unlocks.set([local_locked, cross_race_locked, draft_locked])

    build_bundle(tag)
    tag.refresh_from_db()

    code = bytes(tag.code)
    decrypted = json.loads(
        unseal(derive_wrap_key(code), tag.bundle_blob, aad=tag.bid.encode())
    )
    # Only the same-race non-draft КП should appear
    assert set(decrypted.keys()) == {str(local_locked.id)}
    assert str(cross_race_locked.id) not in decrypted
    assert str(draft_locked.id) not in decrypted


@pytest.mark.django_db
def test_build_bundle_invalid_only_unlocks_produces_none_not_fallback():
    """A tag whose explicit unlocks contain *only* cross-race/draft КП must get
    bundle_blob=None, not silently fall back to [tag.point].

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

    draft_cp = Checkpoint.objects.create(
        race=race_a,
        number=2,
        cost=3,
        description="draft",
        type="draft",
        is_legend_locked=True,
    )
    seal_checkpoint(draft_cp)

    tag = CheckpointTag.objects.create(point=local_cp, nfc_uid="04DEADBEEF")
    # Both explicit unlocks are invalid — cross-race and draft only
    tag.unlocks.set([cross_race_cp, draft_cp])

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
    tag_a = CheckpointTag.objects.create(point=p_a, nfc_uid="0A0A0A0A")
    tag_b = CheckpointTag.objects.create(point=p_b, nfc_uid="0B0B0B0B")
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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")
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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")  # empty unlocks

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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="0B0B0B0B")
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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="0C0C0C0C")

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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="0D0D0D0D")

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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="0E0E0E0E")

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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="0F0F0F0F")

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
    tag_a = CheckpointTag.objects.create(point=holder, nfc_uid="A1A1A1A1")
    tag_b = CheckpointTag.objects.create(point=holder, nfc_uid="B2B2B2B2")

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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")

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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="04A1B2C3")
    tag.unlocks.set([first])

    before = legend_version(race.id)

    tag.unlocks.add(second)  # rebuild folds in `second`'s key + bumps updated_at

    assert legend_version(race.id) != before


@pytest.mark.django_db
def test_signal_draft_to_kp_on_locked_cp_rebuilds_dependent_bundle():
    """Promoting a locked draft КП to kp rebuilds bundles of tags that unlock it.

    A tag's explicit unlocks M2M filtered by build_bundle excludes draft КП, so
    when the CP was a draft the bundle had no content_key for it. After the type
    change the legend serves that CP's enc_blob; without a signal-driven rebuild
    the bundle would remain stale and the app could not decrypt.
    """
    import base64
    import json

    from apps.mobile.crypto import derive_wrap_key, unseal
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Draft promote", slug="draft-promote")
    locked_draft = Checkpoint.objects.create(
        race=race,
        number=1,
        cost=5,
        description="secret",
        type="draft",
        is_legend_locked=True,
    )
    holder = Checkpoint.objects.create(race=race, number=2, cost=1, description="h")
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="DDDDDDDD")
    tag.unlocks.add(locked_draft)  # draft at add time → bundle excludes its key

    tag.refresh_from_db()
    assert tag.bundle_blob is None  # draft КП excluded from bundle

    # Promote to kp — should trigger a bundle rebuild via pre_save/post_save
    locked_draft.type = "kp"
    locked_draft.save()

    tag.refresh_from_db()
    assert tag.bundle_blob is not None  # rebuild happened

    secret = locked_draft.secret
    decrypted = json.loads(
        unseal(derive_wrap_key(bytes(tag.code)), tag.bundle_blob, aad=tag.bid.encode())
    )
    assert decrypted == {
        str(locked_draft.id): base64.b64encode(bytes(secret.content_key)).decode()
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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")

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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")
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
    tag1 = CheckpointTag.objects.create(point=cp1, nfc_uid="0A0A0A0A")
    tag2 = CheckpointTag.objects.create(point=cp2, nfc_uid="0B0B0B0B")
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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="0C0C0C0C")
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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")
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
    tag = CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3")
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
    """An open-КП tag (no bundle_blob) → {bid, point, check_method}, iv/ct None."""
    from apps.mobile.serializers import TagSerializer
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Open tag ser", slug="open-tag-ser")
    cp = Checkpoint.objects.create(race=race, number=1, cost=2, description="open")
    tag = CheckpointTag.objects.create(
        point=cp, nfc_uid="04A1B2C3", check_method="offline"
    )
    tag.refresh_from_db()
    assert tag.bundle_blob is None  # open КП → no unlock envelope

    data = TagSerializer(tag).data
    assert data["bid"] == tag.bid
    assert data["point"] == cp.id
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
        point=cp, nfc_uid="04A1B2C3", check_method="offline"
    )
    tag.refresh_from_db()
    assert tag.bundle_blob is not None  # locked КП → unlock envelope present

    data = TagSerializer(tag).data
    assert data["bid"] == tag.bid
    assert data["point"] == cp.id
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
    open_tag = CheckpointTag.objects.create(point=open_cp, nfc_uid="0A0A0A0A")
    locked_tag = CheckpointTag.objects.create(point=locked_cp, nfc_uid="0B0B0B0B")
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
    tag_a = CheckpointTag.objects.create(point=holder, nfc_uid="AA000001")
    tag_b = CheckpointTag.objects.create(point=holder, nfc_uid="BB000002")
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

    Repro: tag.point=holder, tag.unlocks=[target], delete [holder, target] together.
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
    tag = CheckpointTag.objects.create(point=holder, nfc_uid="CC000003")
    tag.unlocks.set([target])
    tag_pk = tag.pk

    # Must not raise DatabaseError or IntegrityError.
    Checkpoint.objects.filter(pk__in=[holder.pk, target.pk]).delete()

    # The tag was cascade-deleted along with holder.
    assert not CheckpointTag.objects.filter(pk=tag_pk).exists()
