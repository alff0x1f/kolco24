import hashlib
import hmac
import time

import pytest
from django.db import IntegrityError
from django.db.models import Prefetch
from django.test import RequestFactory

from apps.mobile.models import AppAuthFailure, AppInstall
from apps.mobile.permissions import SignedAppPermission, _client_ip
from apps.mobile.signing import build_canonical, sha256_hex, sign, tag_hash, verify


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


def test_tag_hash_matches_hmac():
    expected = hmac.new(b"secret", b"04A1B2C3", hashlib.sha256).hexdigest()
    assert tag_hash("secret", "04A1B2C3") == expected
    # deterministic across calls
    assert tag_hash("secret", "04A1B2C3") == tag_hash("secret", "04A1B2C3")


def test_tag_hash_differs_per_secret():
    assert tag_hash("secret", "04A1B2C3") != tag_hash("other-secret", "04A1B2C3")


@pytest.mark.django_db
def test_legend_tag_serializer_hashes_nfc_uid():
    from apps.mobile.serializers import LegendTagSerializer
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag race", slug="tag-race")
    point = Checkpoint.objects.create(race=race, number=1, cost=1)
    tag = CheckpointTag.objects.create(
        point=point, nfc_uid="04A1B2C3", check_method="offline"
    )

    data = LegendTagSerializer(tag, context={"secret": SECRET}).data

    assert set(data.keys()) == {"id", "tag_hash", "check_method"}
    assert "tag_id" not in data
    assert "nfc_uid" not in data
    assert data["id"] == tag.id
    assert data["check_method"] == "offline"
    assert data["tag_hash"] == tag_hash(SECRET, "04A1B2C3")


@pytest.mark.django_db
def test_legend_checkpoint_serializer_nests_hashed_tags():
    from apps.mobile.serializers import LegendCheckpointSerializer
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(name="Tag race 2", slug="tag-race-2")
    point = Checkpoint.objects.create(race=race, number=1, cost=1)
    CheckpointTag.objects.create(point=point, nfc_uid="DEADBEEF", check_method="online")

    data = LegendCheckpointSerializer(point, context={"secret": SECRET}).data

    assert [t["tag_hash"] for t in data["tags"]] == [tag_hash(SECRET, "DEADBEEF")]
    assert "tag_id" not in data["tags"][0]
    assert "nfc_uid" not in data["tags"][0]


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

    race = Race.objects.create(
        name="Test race", slug="test-race", is_legend_visible=True
    )
    Checkpoint.objects.create(race=race, number=3, cost=2, description="third")
    Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    Checkpoint.objects.create(race=race, number=2, cost=1, description="second")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert data["race"] == race.id
    assert [c["number"] for c in data["checkpoints"]] == [1, 2, 3]
    first = data["checkpoints"][0]
    assert set(first.keys()) == {"id", "number", "cost", "type", "description", "tags"}
    assert first["type"] == "kp"
    assert first["description"] == "first"


# --- End-to-end gate + stats (request-level) -------------------------------


@pytest.fixture
def race_with_checkpoints(db):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="E2E race", slug="e2e-race", is_legend_visible=True)
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
def test_legend_race_deleted_mid_request_returns_404(client, settings, monkeypatch):
    """legend_state returning visible=None (race deleted after 404 check) → 404."""
    import apps.mobile.views as mobile_views
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Ghost", slug="ghost-legend", is_legend_visible=True
    )
    path = f"/app/race/{race.id}/legend/"

    monkeypatch.setattr(
        mobile_views, "legend_state", lambda *a, **kw: ("deadbeef00000000", None)
    )

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
def test_legend_closed_legend_returns_empty_checkpoints(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Closed race", slug="closed-race", is_legend_visible=False
    )
    Checkpoint.objects.create(race=race, number=1, cost=1, description="secret")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    assert data["race"] == race.id
    assert data["checkpoints"] == []


@pytest.mark.django_db
def test_legend_excludes_draft_checkpoints(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Draft race", slug="draft-race", is_legend_visible=True
    )
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    Checkpoint.objects.create(
        race=race, number=2, cost=0, description="draft cp", type="draft"
    )

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    numbers = [c["number"] for c in data["checkpoints"]]
    assert numbers == [1]


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

    race = Race.objects.create(
        name="Stale legend", slug="stale-legend", is_legend_visible=True
    )
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
def test_legend_hidden_response_carries_etag(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Hidden legend", slug="hidden-legend", is_legend_visible=False
    )
    Checkpoint.objects.create(race=race, number=1, cost=1, description="secret")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["checkpoints"] == []
    etag = response["ETag"]
    assert etag.startswith('"') and etag.endswith('"')


@pytest.mark.django_db
def test_legend_hidden_if_none_match_returns_304(client, settings):
    """304 fires even when is_legend_visible=False."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Hidden 304", slug="hidden-304", is_legend_visible=False
    )
    Checkpoint.objects.create(race=race, number=1, cost=1, description="secret")

    path = f"/app/race/{race.id}/legend/"
    first = client.get(path, **_signed_headers("GET", path, SECRET))
    etag = first["ETag"]

    headers = _signed_headers("GET", path, SECRET)
    headers["HTTP_IF_NONE_MATCH"] = etag
    second = client.get(path, **headers)

    assert second.status_code == 304
    assert second["ETag"] == etag
    assert second.content == b""


@pytest.mark.django_db
def test_legend_serves_hashed_tags_and_never_raw_tag_id(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Hashed legend", slug="hashed-legend", is_legend_visible=True
    )
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")
    CheckpointTag.objects.create(point=cp, nfc_uid="DEADBEEF", check_method="online")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    data = response.json()
    tags = data["checkpoints"][0]["tags"]
    assert [t["tag_hash"] for t in tags] == [
        tag_hash(SECRET, "04A1B2C3"),
        tag_hash(SECRET, "DEADBEEF"),
    ]
    for t in tags:
        assert set(t.keys()) == {"id", "tag_hash", "check_method"}
        assert "tag_id" not in t
        assert "nfc_uid" not in t
    # the raw UID never appears anywhere in the serialized body
    body = response.content.decode()
    assert "04A1B2C3" not in body
    assert "DEADBEEF" not in body


@pytest.mark.django_db
def test_legend_etag_changes_when_tag_edited_and_304_with_new_etag(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(name="Tag etag", slug="tag-etag", is_legend_visible=True)
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

    race = Race.objects.create(
        name="Tag etag uf", slug="tag-etag-uf", is_legend_visible=True
    )
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
def test_legend_different_key_ids_get_different_hashes_and_etags(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"build-a": "secret-a", "build-b": "secret-b"}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Per build", slug="per-build-legend", is_legend_visible=True
    )
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")

    path = f"/app/race/{race.id}/legend/"
    resp_a = client.get(
        path, **_signed_headers("GET", path, "secret-a", key_id="build-a")
    )
    resp_b = client.get(
        path, **_signed_headers("GET", path, "secret-b", key_id="build-b")
    )

    hash_a = resp_a.json()["checkpoints"][0]["tags"][0]["tag_hash"]
    hash_b = resp_b.json()["checkpoints"][0]["tags"][0]["tag_hash"]
    assert hash_a == tag_hash("secret-a", "04A1B2C3")
    assert hash_b == tag_hash("secret-b", "04A1B2C3")
    assert hash_a != hash_b
    assert resp_a["ETag"] != resp_b["ETag"]


@pytest.mark.django_db
def test_legend_hidden_still_200_empty_with_etag_when_tags_present(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"test-v1": SECRET}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Hidden with tags", slug="hidden-tags", is_legend_visible=False
    )
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="secret")
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")

    path = f"/app/race/{race.id}/legend/"
    response = client.get(path, **_signed_headers("GET", path, SECRET))

    assert response.status_code == 200
    assert response.json()["checkpoints"] == []
    etag = response["ETag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert "04A1B2C3" not in response.content.decode()


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
    "is_legend_visible",
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

    race = Race.objects.create(
        name="Empty legend", slug="empty-legend", is_legend_visible=True
    )
    first = legend_version(race.id)
    second = legend_version(race.id)
    assert first == second
    assert first  # non-empty


@pytest.mark.django_db
def test_legend_version_changes_when_checkpoint_description_edited():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(
        name="Edit legend", slug="edit-legend", is_legend_visible=True
    )
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

    race = Race.objects.create(
        name="Add legend", slug="add-legend", is_legend_visible=True
    )
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

    race = Race.objects.create(
        name="Remove legend", slug="remove-legend", is_legend_visible=True
    )
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

    race = Race.objects.create(
        name="Kp to draft", slug="kp-to-draft", is_legend_visible=True
    )
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

    race = Race.objects.create(
        name="Draft to kp", slug="draft-to-kp", is_legend_visible=True
    )
    cp = Checkpoint.objects.create(
        race=race, number=1, cost=1, description="cp", type="draft"
    )
    before = legend_version(race.id)
    cp.type = "kp"
    cp.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_changes_when_is_legend_visible_toggled():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(
        name="Toggle legend", slug="toggle-legend", is_legend_visible=True
    )
    Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    before = legend_version(race.id)
    race.is_legend_visible = False
    race.save()
    after = legend_version(race.id)
    assert before != after


@pytest.mark.django_db
def test_legend_version_unchanged_when_draft_checkpoint_edited():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(
        name="Draft edit", slug="draft-edit", is_legend_visible=True
    )
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

    race = Race.objects.create(name="Tag edit", slug="tag-edit", is_legend_visible=True)
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

    race = Race.objects.create(name="Tag add", slug="tag-add", is_legend_visible=True)
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
def test_legend_version_unchanged_when_tag_on_draft_checkpoint_added():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(
        name="Tag draft", slug="tag-draft", is_legend_visible=True
    )
    Checkpoint.objects.create(race=race, number=1, cost=1, description="visible")
    draft = Checkpoint.objects.create(
        race=race, number=2, cost=0, description="draft", type="draft"
    )
    before = legend_version(race.id)
    CheckpointTag.objects.create(point=draft, nfc_uid="AA:BB", check_method="offline")
    after = legend_version(race.id)
    assert before == after


@pytest.mark.django_db
def test_legend_version_differs_per_key_id():
    from apps.mobile.versioning import legend_version
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    race = Race.objects.create(
        name="Key id legend", slug="key-id-legend", is_legend_visible=True
    )
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="cp")
    CheckpointTag.objects.create(point=cp, nfc_uid="AA:BB", check_method="offline")
    assert legend_version(race.id, "build-a") != legend_version(race.id, "build-b")


@pytest.mark.django_db
def test_legend_version_tagless_race_stable_across_key_ids():
    from apps.mobile.versioning import legend_version
    from website.models.race import Race

    race = Race.objects.create(
        name="Tagless", slug="tagless-legend", is_legend_visible=True
    )
    # No checkpoints/tags: empty aggregates render "None"; must not crash and
    # must still be deterministic per key_id.
    assert legend_version(race.id, "build-a") == legend_version(race.id, "build-a")
    assert legend_version(race.id, "build-a") != legend_version(race.id, "build-b")


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

    race = Race.objects.create(
        name="Sync legend", slug="sync-legend-etag", is_legend_visible=True
    )
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
    # (unquoted) for the *same* key_id — both fold key_id into the fingerprint.
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"build-a": "secret-a", "build-b": "secret-b"}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Sync per build", slug="sync-legend-per-key", is_legend_visible=True
    )
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
def test_sync_versions_legend_differs_across_key_ids(client, settings):
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    settings.MOBILE_APP_KEYS = {"build-a": "secret-a", "build-b": "secret-b"}
    settings.MOBILE_APP_TS_WINDOW = 300

    race = Race.objects.create(
        name="Sync diff build", slug="sync-legend-diff-key", is_legend_visible=True
    )
    cp = Checkpoint.objects.create(race=race, number=1, cost=1, description="first")
    CheckpointTag.objects.create(point=cp, nfc_uid="04A1B2C3", check_method="offline")

    sync_path = f"/app/race/{race.id}/sync/"
    resp_a = client.get(
        sync_path, **_signed_headers("GET", sync_path, "secret-a", key_id="build-a")
    )
    resp_b = client.get(
        sync_path, **_signed_headers("GET", sync_path, "secret-b", key_id="build-b")
    )

    assert resp_a.json()["versions"]["legend"] != resp_b.json()["versions"]["legend"]


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
