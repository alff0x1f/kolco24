import hashlib
import hmac
import time

import pytest
from django.db import IntegrityError
from django.db.models import Prefetch
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


def test_permission_none_secret_fails_closed(settings):
    settings.MOBILE_APP_SECRET = None
    request = _signed_get_request()
    assert SignedAppPermission().has_permission(request, None) is False


def test_permission_missing_headers_false(settings):
    settings.MOBILE_APP_SECRET = SECRET
    request = RequestFactory().get(PATH)
    assert SignedAppPermission().has_permission(request, None) is False


@pytest.mark.parametrize("drop_header", ["X-App-Sig", "X-App-Ts", "X-Install-Id"])
def test_permission_each_required_header_individually_false(settings, drop_header):
    settings.MOBILE_APP_SECRET = SECRET
    ts = str(int(time.time()))
    canonical = build_canonical("GET", PATH, ts, b"")
    headers = {
        "X-App-Sig": sign(SECRET, canonical),
        "X-App-Ts": ts,
        "X-Install-Id": "install-abc",
    }
    del headers[drop_header]
    request = RequestFactory().get(PATH, headers=headers)
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


def test_permission_future_ts_false(settings):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300
    future_ts = str(int(time.time()) + 1000)
    request = _signed_get_request(ts=future_ts)
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


def test_client_ip_invalid_forwarded_for_falls_back_to_remote_addr():
    request = RequestFactory().get(
        PATH,
        headers={"X-Forwarded-For": "not-an-ip"},
        REMOTE_ADDR="10.0.0.2",
    )
    assert _client_ip(request) == "10.0.0.2"


# --- LegendView request-level ----------------------------------------------


def _signed_headers(method, path, secret, body=b""):
    """Build signed request headers mirroring the client side."""
    ts = str(int(time.time()))
    canonical = build_canonical(method, path, ts, body)
    sig = sign(secret, canonical)
    return {
        "HTTP_X_APP_SIG": sig,
        "HTTP_X_APP_TS": ts,
        "HTTP_X_INSTALL_ID": "install-abc",
        "HTTP_X_APP_PLATFORM": "ios",
        "HTTP_X_APP_VERSION": "1.4.0",
    }


@pytest.mark.django_db
def test_legend_valid_signature_returns_200_with_fields_and_order(client, settings):
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    settings.MOBILE_APP_SECRET = SECRET
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
    assert set(first.keys()) == {"number", "cost", "type", "description"}
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
    settings.MOBILE_APP_SECRET = SECRET
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    response = client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_wrong_signature_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_SECRET = SECRET
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    headers = _signed_headers("GET", path, "wrong-secret")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_expired_ts_returns_403(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    old_ts = str(int(time.time()) - 1000)
    canonical = build_canonical("GET", path, old_ts, b"")
    sig = sign(SECRET, canonical)
    headers = {
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
def test_legend_empty_secret_fails_closed(client, settings, race_with_checkpoints):
    settings.MOBILE_APP_SECRET = ""
    path = f"/app/race/{race_with_checkpoints.id}/legend/"
    # even a "correctly" signed request (against empty secret) must be rejected
    headers = _signed_headers("GET", path, "")
    response = client.get(path, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_valid_sig_nonexistent_race_returns_404(client, settings):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300
    path = "/app/race/999999/legend/"
    headers = _signed_headers("GET", path, SECRET)
    response = client.get(path, **headers)
    assert response.status_code == 404


@pytest.mark.django_db
def test_legend_records_appinstall_and_increments(
    client, settings, race_with_checkpoints
):
    settings.MOBILE_APP_SECRET = SECRET
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

    settings.MOBILE_APP_SECRET = SECRET
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

    settings.MOBILE_APP_SECRET = SECRET
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

    settings.MOBILE_APP_SECRET = SECRET
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

    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300

    Race.objects.create(name="Hidden", slug="hidden", is_published=False)

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    assert response.json() == {"races": []}


@pytest.mark.django_db
def test_races_no_headers_returns_403(client, settings):
    settings.MOBILE_APP_SECRET = SECRET
    response = client.get(RACES_PATH)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_races_wrong_signature_returns_403(client, settings):
    settings.MOBILE_APP_SECRET = SECRET
    headers = _signed_headers("GET", RACES_PATH, "wrong-secret")
    response = client.get(RACES_PATH, **headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.django_db
def test_legend_stats_write_failure_does_not_break_response(
    client, settings, race_with_checkpoints, monkeypatch
):
    settings.MOBILE_APP_SECRET = SECRET
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
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    install = AppInstall.objects.get(install_id="install-abc")
    assert install.request_count == 1


@pytest.mark.django_db
def test_races_stats_write_failure_does_not_break_response(
    client, settings, monkeypatch
):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300

    def boom(*args, **kwargs):
        raise IntegrityError("simulated stats write failure")

    monkeypatch.setattr(AppInstall.objects, "update_or_create", boom)

    response = client.get(RACES_PATH, **_signed_headers("GET", RACES_PATH, SECRET))

    assert response.status_code == 200
    assert response.json() == {"races": []}
    assert not AppInstall.objects.filter(install_id="install-abc").exists()


@pytest.mark.django_db
def test_races_tampered_query_string_returns_403(client, settings):
    settings.MOBILE_APP_SECRET = SECRET
    settings.MOBILE_APP_TS_WINDOW = 300
    # Signature covers the bare path; appending a query string must be rejected
    headers = _signed_headers("GET", RACES_PATH, SECRET)
    response = client.get(RACES_PATH + "?page=2", **headers)
    assert response.status_code == 403


# --- teams_version fingerprint ----------------------------------------------


def _make_race_with_category(name="Versioned race", slug="versioned-race"):
    from website.models.race import Category, Race

    race = Race.objects.create(name=name, slug=slug)
    category = Category.objects.create(code="open", name="Open", race=race)
    return race, category


@pytest.mark.django_db
def test_teams_version_stable_for_empty_race():
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


# --- mobile TeamSerializer --------------------------------------------------

TEAM_FIELDS = {
    "id",
    "teamname",
    "category2",
    "ucount",
    "paid_people",
    "start_time",
    "finish_time",
    "members",
}
MEMBER_FIELDS = {"name", "birth", "number_in_team"}


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
