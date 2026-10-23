import io
import math
import xml.etree.ElementTree as ET
import zipfile

import pytest
from django.urls import reverse

from apps.mobile.models import TrackPoint
from apps.race.tests import _make_category, _make_race, _make_team
from website.models.race import RaceAdmin

GPX_NS = {"g": "http://www.topografix.com/GPX/1/1"}
T0 = 1_700_000_000_000
M_PER_DEG = 6_371_000.0 * math.pi / 180.0


def _admin(django_user_model, race, username="adm"):
    user = django_user_model.objects.create_user(username=username, password="x")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    return user


def _point(team, point_id, t_sec, north_m=0.0, **kwargs):
    defaults = {
        "install_id": "phone-a",
        "segment_id": "seg-1",
        "lat": 55.0 + north_m / M_PER_DEG,
        "lon": 37.0,
        "accuracy": 10.0,
        "gps_time_ms": T0 + t_sec * 1000,
        "elapsed_at": t_sec * 1000,
    }
    defaults.update(kwargs)
    return TrackPoint.objects.create(
        id=point_id, team=team, race=team.category2.race, **defaults
    )


def _walk(team, prefix, n, start_sec, start_m=0.0, **kwargs):
    for i in range(n):
        _point(team, f"{prefix}{i}", start_sec + 15 * i, start_m + 20.0 * i, **kwargs)


def _download(client, race):
    resp = client.get(reverse("race_map_gpx_all", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _tracks(archive, path):
    root = ET.fromstring(archive.read(path))
    return root, [
        (
            trk.findtext("g:name", namespaces=GPX_NS),
            [
                len(seg.findall("g:trkpt", GPX_NS))
                for seg in trk.findall("g:trkseg", GPX_NS)
            ],
        )
        for trk in root.findall("g:trk", GPX_NS)
    ]


@pytest.fixture
def race():
    return _make_race()


@pytest.fixture
def category(race):
    return _make_category(race)


@pytest.fixture
def admin_client(client, django_user_model, race):
    client.force_login(_admin(django_user_model, race))
    return client


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="x")


# ---- access ----


@pytest.mark.django_db
def test_anonymous_redirects_to_login(client, race):
    url = reverse("race_map_gpx_all", kwargs={"race_slug": race.slug})
    resp = client.get(url)
    assert resp.status_code == 302
    assert resp.url.startswith(reverse("login"))
    assert "next=" in resp.url


@pytest.mark.django_db
def test_plain_user_forbidden(client, django_user_model, race):
    client.force_login(django_user_model.objects.create_user(username="u"))
    resp = client.get(reverse("race_map_gpx_all", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_bare_superuser_forbidden(client, django_user_model, race):
    client.force_login(
        django_user_model.objects.create_superuser(username="root", password="x")
    )
    resp = client.get(reverse("race_map_gpx_all", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_gets_named_zip(admin_client, race):
    resp = admin_client.get(
        reverse("race_map_gpx_all", kwargs={"race_slug": race.slug})
    )
    assert resp.status_code == 200
    assert (
        resp["Content-Disposition"] == f'attachment; filename="{race.slug}-tracks.zip"'
    )
    assert zipfile.ZipFile(io.BytesIO(resp.content)).namelist() == []


# ---- content ----


@pytest.mark.django_db
def test_single_phone_track_with_spike_filtered(admin_client, race, category, owner):
    team = _make_team(owner, category, start_number="12", teamname="Лоси")
    _walk(team, "a", 5, 0)
    _point(team, "spike", 75, 80.0 + 600.0, accuracy=450.0)
    _walk(team, "b", 5, 90, 120.0)
    _point(team, "ele", 165, 220.0, altitude=150.5)

    archive = _download(admin_client, race)
    path = f"12h/team-12-{team.id}.gpx"
    assert archive.namelist() == [path]
    root, tracks = _tracks(archive, path)
    assert root.findtext("g:metadata/g:name", namespaces=GPX_NS) == (
        "Teams Race — №12 Лоси"
    )
    assert tracks == [("№12 Лоси", [11])]
    points = root.findall(".//g:trkpt", GPX_NS)
    assert points[-1].findtext("g:ele", namespaces=GPX_NS) == "150.5"
    assert [child.tag.split("}")[1] for child in points[-1]] == ["ele", "time"]
    assert points[0].findtext("g:time", namespaces=GPX_NS) == "2023-11-14T22:13:20.000Z"


@pytest.mark.django_db
def test_two_phones_two_tracks_ordered_by_first_point(
    admin_client, race, category, owner
):
    team = _make_team(owner, category, start_number="7")
    _walk(team, "late", 3, 100, install_id="aaa")
    _walk(team, "early", 4, 0, 5000.0, install_id="zzz", segment_id="seg-z")

    archive = _download(admin_client, race)
    _, tracks = _tracks(archive, f"12h/team-7-{team.id}.gpx")
    assert tracks == [("№7 · телефон 1", [4]), ("№7 · телефон 2", [3])]


@pytest.mark.django_db
def test_segments_become_separate_trksegs(admin_client, race, category, owner):
    team = _make_team(owner, category, start_number="3")
    _walk(team, "a", 3, 0, segment_id="s1")
    _walk(team, "b", 2, 60, 60.0, segment_id="s2")

    archive = _download(admin_client, race)
    _, tracks = _tracks(archive, f"12h/team-3-{team.id}.gpx")
    assert tracks == [("№3", [3, 2])]


# ---- time ----


@pytest.mark.django_db
def test_window_bounds_trim_exactly(admin_client, race, category, owner):
    team = _make_team(
        owner,
        category,
        start_number="1",
        start_time=T0 + 1_000_000,
        finish_time=T0 + 2_000_000,
    )
    _point(team, "before", 399, 0.0)
    _point(team, "lo", 400, 10.0)
    _point(team, "hi", 2600, 20.0)
    _point(team, "after", 2601, 30.0)

    archive = _download(admin_client, race)
    _, tracks = _tracks(archive, f"12h/team-1-{team.id}.gpx")
    assert sum(sum(segs) for _, segs in tracks) == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "start,finish,kept",
    [
        (0, T0 + 1_000_000, 2),  # open left: early + mid
        (T0 + 1_000_000, 0, 2),  # open right: mid + late
        (0, 0, 3),
    ],
)
def test_unset_bound_is_open(admin_client, race, category, owner, start, finish, kept):
    team = _make_team(
        owner, category, start_number="1", start_time=start, finish_time=finish
    )
    _point(team, "early", 0, 0.0)
    _point(team, "mid", 1000, 10.0)
    _point(team, "late", 100_000, 20.0)

    archive = _download(admin_client, race)
    _, tracks = _tracks(archive, f"12h/team-1-{team.id}.gpx")
    assert sum(sum(segs) for _, segs in tracks) == kept


@pytest.mark.django_db
def test_trusted_time_used_and_zero_treated_as_absent(
    admin_client, race, category, owner
):
    team = _make_team(owner, category, start_number="1")
    _point(team, "trusted", 0, 0.0, trusted_ms=T0 + 3_600_000)
    _point(team, "zero", 15, 20.0, trusted_ms=0)

    archive = _download(admin_client, race)
    root, _ = _tracks(archive, f"12h/team-1-{team.id}.gpx")
    times = [
        p.findtext("g:time", namespaces=GPX_NS)
        for p in root.iter(f"{{{GPX_NS['g']}}}trkpt")
    ]
    assert times == ["2023-11-14T22:13:35.000Z", "2023-11-14T23:13:20.000Z"]


@pytest.mark.django_db
def test_point_without_any_time_dropped(admin_client, race, category, owner):
    team = _make_team(owner, category, start_number="1")
    _point(team, "notime", 0, 0.0, gps_time_ms=0)
    _point(team, "ok", 15, 20.0)

    archive = _download(admin_client, race)
    _, tracks = _tracks(archive, f"12h/team-1-{team.id}.gpx")
    assert tracks == [("№1", [1])]


# ---- team set and naming ----


@pytest.mark.django_db
def test_teams_without_points_out_of_window_or_deleted_skipped(
    admin_client, race, category, owner
):
    _make_team(owner, category, start_number="1")
    outside = _make_team(owner, category, start_number="2", start_time=T0 + 9_000_000)
    _point(outside, "o", 0)
    deleted = _make_team(owner, category, start_number="3", is_deleted=True)
    _point(deleted, "d", 0)

    assert _download(admin_client, race).namelist() == []


@pytest.mark.django_db
def test_other_race_not_leaked(admin_client, race, category, owner):
    other = _make_race(slug="other")
    other_team = _make_team(owner, _make_category(other), start_number="9")
    _point(other_team, "x", 0)

    assert _download(admin_client, race).namelist() == []


@pytest.mark.django_db
def test_file_names_unique_and_sanitized(admin_client, race, category, owner):
    a = _make_team(owner, category, start_number="5")
    b = _make_team(owner, category, start_number="5")
    c = _make_team(owner, category, start_number="№ ")
    for team in (a, b, c):
        _point(team, f"p{team.id}", 0)

    names = sorted(_download(admin_client, race).namelist())
    assert names == sorted(
        [f"12h/team-5-{a.id}.gpx", f"12h/team-5-{b.id}.gpx", f"12h/team-{c.id}.gpx"]
    )


@pytest.mark.django_db
def test_category_folders(admin_client, race, owner):
    plain = _make_category(race, code="6h")
    cyrillic = _make_category(race, code="Вело")
    dup1 = _make_category(race, code="24h")
    dup2 = _make_category(race, code="24h")
    slash = _make_category(race, code="a/b")
    empty = _make_category(race, code="/")
    teams = {
        cat.id: _make_team(owner, cat, start_number=str(i))
        for i, cat in enumerate([plain, cyrillic, dup1, dup2, slash, empty], start=1)
    }
    for team in teams.values():
        _point(team, f"p{team.id}", 0)

    folders = {n.split("/")[0] for n in _download(admin_client, race).namelist()}
    assert folders == {
        "6h",
        "Вело",
        f"24h-{dup1.id}",
        f"24h-{dup2.id}",
        "ab",
        f"category-{empty.id}",
    }


@pytest.mark.django_db
def test_map_page_links_to_zip(admin_client, race):
    resp = admin_client.get(reverse("race_map", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200
    url = reverse("race_map_gpx_all", kwargs={"race_slug": race.slug})
    assert f'href="{url}"' in resp.content.decode()
