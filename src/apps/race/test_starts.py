import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.race.tests import _make_category, _make_race, _make_team, _script_json
from website.models.race import RaceAdmin

STARTS_URLS = ["race_starts", "race_starts_data"]


def _admin(django_user_model, race, username="adm", role=RaceAdmin.Role.ADMIN):
    user = django_user_model.objects.create_user(username=username, password="x")
    RaceAdmin.objects.create(race=race, user=user, role=role)
    return user


def _data(client, race):
    resp = client.get(reverse("race_starts_data", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.django_db
@pytest.mark.parametrize("name", STARTS_URLS)
def test_starts_anonymous_redirects_to_login(client, name):
    race = _make_race()
    url = reverse(name, kwargs={"race_slug": race.slug})
    resp = client.get(url)
    assert resp.status_code == 302
    assert resp.url.startswith(reverse("login"))
    assert "next=" in resp.url


@pytest.mark.django_db
@pytest.mark.parametrize("name", STARTS_URLS)
def test_starts_plain_user_forbidden(client, django_user_model, name):
    race = _make_race()
    client.force_login(django_user_model.objects.create_user(username="u"))
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", STARTS_URLS)
def test_starts_moderator_forbidden(client, django_user_model, name):
    race = _make_race()
    client.force_login(_admin(django_user_model, race, role=RaceAdmin.Role.MODERATOR))
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", STARTS_URLS)
def test_starts_bare_superuser_forbidden(client, django_user_model, name):
    race = _make_race()
    client.force_login(
        django_user_model.objects.create_superuser(username="root", password="x")
    )
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", STARTS_URLS)
def test_starts_race_admin_allowed(client, django_user_model, name):
    race = _make_race()
    client.force_login(_admin(django_user_model, race))
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_starts_data_lists_only_paid_live_teams_of_race(client, django_user_model):
    race = _make_race()
    category = _make_category(race)
    admin = _admin(django_user_model, race)
    paid = _make_team(admin, category, start_number="1", teamname="Оплачена")
    _make_team(admin, category, start_number="2", teamname="Нет", paid_people=0)
    deleted = _make_team(admin, category, start_number="3", teamname="Удалена")
    deleted.is_deleted = True
    deleted.save(update_fields=["is_deleted", "updated_at"])
    other = _make_race(slug="other")
    _make_team(admin, _make_category(other), start_number="4", teamname="Чужая")
    client.force_login(admin)

    data = _data(client, race)

    assert [t["id"] for t in data["teams"]] == [paid.id]
    row = data["teams"][0]
    assert row["name"] == "Оплачена"
    assert row["category_id"] == category.id
    assert row["paid_people"] == 2
    assert isinstance(row["paid_people"], int)


@pytest.mark.django_db
def test_starts_data_sorts_by_start_number_numerically(client, django_user_model):
    race = _make_race()
    category = _make_category(race)
    admin = _admin(django_user_model, race)
    for number in ["10", "Б1", "9"]:
        _make_team(admin, category, start_number=number)
    client.force_login(admin)

    data = _data(client, race)

    assert [t["start_number"] for t in data["teams"]] == ["9", "10", "Б1"]


@pytest.mark.django_db
@pytest.mark.parametrize("start_time", [0, -5000, 2**62, 253402297200000])
def test_starts_data_unusable_start_time_is_null(client, django_user_model, start_time):
    race = _make_race()
    category = _make_category(race)
    admin = _admin(django_user_model, race)
    _make_team(admin, category, start_time=start_time)
    client.force_login(admin)

    row = _data(client, race)["teams"][0]

    assert row["start_time_ms"] is None
    assert row["start_time"] is None


@pytest.mark.django_db
def test_starts_data_formats_start_time_in_project_timezone(client, django_user_model):
    race = _make_race()
    category = _make_category(race)
    admin = _admin(django_user_model, race)
    moment = timezone.make_aware(datetime.datetime(2026, 10, 8, 9, 14, 5))
    ms = int(moment.timestamp() * 1000)
    _make_team(admin, category, start_time=ms)
    client.force_login(admin)

    row = _data(client, race)["teams"][0]

    assert row["start_time_ms"] == ms
    assert row["start_time"] == "09:14:05"


@pytest.mark.django_db
def test_starts_data_server_time_and_categories(client, django_user_model):
    race = _make_race()
    twelve = _make_category(race)
    six = _make_category(race, code="6h", short_name="6ч", name="6 часов", order=1)
    _make_category(_make_race(slug="other"), code="x")
    admin = _admin(django_user_model, race)
    client.force_login(admin)

    before = int(timezone.now().timestamp() * 1000)
    data = _data(client, race)
    after = int(timezone.now().timestamp() * 1000)

    assert before <= data["server_time_ms"] <= after
    assert re.fullmatch(r"\d\d:\d\d:\d\d", data["server_time"])
    assert data["categories"] == [
        {"id": twelve.id, "code": "12h", "name": "12 часов"},
        {"id": six.id, "code": "6h", "name": "6 часов"},
    ]
    assert data["teams"] == []


@pytest.mark.django_db
def test_starts_page_embeds_data_url(client, django_user_model):
    race = _make_race()
    client.force_login(_admin(django_user_model, race))

    resp = client.get(reverse("race_starts", kwargs={"race_slug": race.slug}))

    config = _script_json(resp.content.decode(), "raceStartsConfig")
    assert config == {
        "dataUrl": reverse("race_starts_data", kwargs={"race_slug": race.slug})
    }


@pytest.mark.django_db
def test_race_page_starts_button_only_for_race_admin(client, django_user_model):
    race = _make_race()
    race.is_published = True
    race.save()
    url = reverse("race", kwargs={"race_slug": race.slug})
    starts_url = reverse("race_starts", kwargs={"race_slug": race.slug})

    client.force_login(django_user_model.objects.create_user(username="u"))
    assert starts_url not in client.get(url).content.decode()

    client.force_login(_admin(django_user_model, race))
    assert starts_url in client.get(url).content.decode()
