import datetime
import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.urls import resolve, reverse

from apps.race.forms import RaceForm
from apps.race.permissions import can_edit_race
from apps.race.views import RaceEditView, RacePageView, RaceTeamsView
from website.models import Race
from website.models.models import Team
from website.models.race import Category, RaceAdmin, RacePriceTier, RegStatus


def _script_json(html, script_id):
    """Extract and parse the JSON embedded in a <script id="..."> block."""
    match = re.search(
        r'<script id="%s" type="application/json">(.*?)</script>' % script_id,
        html,
        re.DOTALL,
    )
    assert match, f"script block {script_id!r} not found"
    return json.loads(match.group(1))


def _make_race(slug="teams-race", code="tr-teams"):
    return Race.objects.create(name="Teams Race", code=code, slug=slug)


def _make_category(race, code="12h", short_name="12ч", name="12 часов", order=0):
    return Category.objects.create(
        code=code,
        name=name,
        short_name=short_name,
        race=race,
        order=order,
    )


def _make_team(owner, category, **kwargs):
    defaults = {
        "paid_people": 2,
        "ucount": 2,
        "start_number": "1",
    }
    defaults.update(kwargs)
    return Team.objects.create(owner=owner, category2=category, **defaults)


@pytest.mark.django_db
def test_build_context_anon_sees_paid_only():
    owner = User.objects.create_user(
        username="o1", password="p", email="o1@example.com"
    )
    race = _make_race()
    cat = _make_category(race)
    paid = _make_team(owner, cat, teamname="Paid", paid_people=2)
    _make_team(owner, cat, teamname="Unpaid", paid_people=0)

    context = RaceTeamsView.build_context(race, AnonymousUser())
    teams = json.loads(context["teams_json"])

    names = {t["name"] for t in teams}
    assert names == {"Paid"}
    assert len(teams) == 1
    assert teams[0]["catId"] == cat.id
    # anon: nothing is mine, nothing is editable
    assert teams[0]["mine"] is False
    assert "edit" not in teams[0]
    # paid (== ucount) so count display is the plain number
    assert teams[0]["cnt"] == "2"
    assert paid.teamname == "Paid"


@pytest.mark.django_db
def test_build_context_owner_sees_own_unpaid():
    owner = User.objects.create_user(
        username="o2", password="p", email="o2@example.com"
    )
    other = User.objects.create_user(
        username="x2", password="p", email="x2@example.com"
    )
    race = _make_race(slug="r2", code="r2")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="MyUnpaid", paid_people=0)
    _make_team(other, cat, teamname="OtherPaid", paid_people=3, ucount=3)
    _make_team(other, cat, teamname="OtherUnpaid", paid_people=0)

    context = RaceTeamsView.build_context(race, owner)
    teams = json.loads(context["teams_json"])
    names = {t["name"] for t in teams}

    # owner sees: their own unpaid team + everyone's paid teams,
    # but NOT another owner's unpaid team
    assert names == {"MyUnpaid", "OtherPaid"}


@pytest.mark.django_db
def test_build_context_superuser_sees_all():
    owner = User.objects.create_user(
        username="o3", password="p", email="o3@example.com"
    )
    admin = User.objects.create_superuser(
        username="a3", password="p", email="a3@example.com"
    )
    race = _make_race(slug="r3", code="r3")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Paid", paid_people=2)
    _make_team(owner, cat, teamname="Unpaid", paid_people=0)

    context = RaceTeamsView.build_context(race, admin)
    teams = json.loads(context["teams_json"])
    names = {t["name"] for t in teams}

    assert names == {"Paid", "Unpaid"}
    # superuser may edit every team
    assert all("edit" in t for t in teams)


@pytest.mark.django_db
def test_mine_and_edit_flags():
    owner = User.objects.create_user(
        username="o4", password="p", email="o4@example.com"
    )
    other = User.objects.create_user(
        username="x4", password="p", email="x4@example.com"
    )
    race = _make_race(slug="r4", code="r4")
    cat = _make_category(race)
    mine_team = _make_team(owner, cat, teamname="Mine", paid_people=2)
    _make_team(other, cat, teamname="Theirs", paid_people=2)

    context = RaceTeamsView.build_context(race, owner)
    teams = {t["name"]: t for t in json.loads(context["teams_json"])}

    assert teams["Mine"]["mine"] is True
    assert teams["Mine"]["edit"] == f"/team/{mine_team.id}"
    assert teams["Theirs"]["mine"] is False
    assert "edit" not in teams["Theirs"]


@pytest.mark.django_db
def test_participants_string_clean_join():
    owner = User.objects.create_user(
        username="o5", password="p", email="o5@example.com"
    )
    race = _make_race(slug="r5", code="r5")
    cat = _make_category(race)
    _make_team(
        owner,
        cat,
        teamname="P",
        paid_people=2,
        athlet1="Иван",
        athlet2="Петр",
        athlet4="Сидор",
    )

    context = RaceTeamsView.build_context(race, AnonymousUser())
    team = json.loads(context["teams_json"])[0]

    # empty slots skipped, clean ", " join
    assert team["parts"] == "Иван, Петр, Сидор"


@pytest.mark.django_db
def test_name_fallback_when_no_teamname():
    owner = User.objects.create_user(
        username="o6",
        password="p",
        email="o6@example.com",
        first_name="Иван",
        last_name="Петров",
    )
    race = _make_race(slug="r6", code="r6")
    cat = _make_category(race)
    team = _make_team(owner, cat, teamname="", paid_people=2)

    context = RaceTeamsView.build_context(race, AnonymousUser())
    row = json.loads(context["teams_json"])[0]

    assert row["name"] == f"Без названия {team.id} (Петров Иван)"


@pytest.mark.django_db
def test_cnt_display_when_paid_differs_from_ucount():
    owner = User.objects.create_user(
        username="o7", password="p", email="o7@example.com"
    )
    race = _make_race(slug="r7", code="r7")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Partial", paid_people=1, ucount=3)

    context = RaceTeamsView.build_context(race, AnonymousUser())
    row = json.loads(context["teams_json"])[0]

    assert row["cnt"] == "1/3"


@pytest.mark.django_db
def test_color_idx_wraps_at_eight():
    owner = User.objects.create_user(
        username="o8", password="p", email="o8@example.com"
    )
    race = _make_race(slug="r8", code="r8")
    for i in range(9):
        _make_category(race, code=f"c{i}", short_name=f"c{i}", order=i)

    context = RaceTeamsView.build_context(race, owner)
    cats = json.loads(context["categories_json"])

    assert len(cats) == 9
    for idx, cat in enumerate(cats):
        assert cat["colorIdx"] == idx % 8
    # 9th category (index 8) wraps back to 0
    assert cats[8]["colorIdx"] == 0


@pytest.mark.django_db
def test_category_with_zero_paid_teams_count_is_zero():
    owner = User.objects.create_user(
        username="o9", password="p", email="o9@example.com"
    )
    race = _make_race(slug="r9", code="r9")
    _make_category(race, code="empty", short_name="empty", order=0)
    full_cat = _make_category(race, code="full", short_name="full", order=1)
    _make_team(owner, full_cat, teamname="T", paid_people=2)

    context = RaceTeamsView.build_context(race, AnonymousUser())
    cats = {c["label"]: c for c in json.loads(context["categories_json"])}

    # Subquery returns None for the empty category; must be coerced to 0
    assert cats["empty"]["count"] == 0
    assert cats["full"]["count"] == 1
    assert cats["empty"]["label"] == "empty"


@pytest.mark.django_db
def test_summary_uses_model_helpers():
    owner = User.objects.create_user(
        username="o10", password="p", email="o10@example.com"
    )
    race = _make_race(slug="r10", code="r10")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="A", paid_people=2)
    _make_team(owner, cat, teamname="B", paid_people=3, ucount=3)
    _make_team(owner, cat, teamname="Unpaid", paid_people=0)

    context = RaceTeamsView.build_context(race, AnonymousUser())

    assert context["race_team_count"] == 2
    assert context["race_people_count"] == 5
    assert context["category_count"] == 1
    assert context["race_date"] == race.date


@pytest.mark.django_db
def test_urls_resolve_to_race_teams_view():
    race = _make_race(slug="ru1", code="ru1")
    cat = _make_category(race)

    for url in (
        reverse("all_teams", args=[race.slug]),
        reverse("my_teams", args=[race.slug]),
        reverse("teams2", args=[race.slug, cat.id]),
    ):
        assert resolve(url).func.view_class is RaceTeamsView


@pytest.mark.django_db
def test_all_teams_returns_200_with_data_initial_all(client):
    owner = User.objects.create_user(
        username="ru2", password="p", email="ru2@example.com"
    )
    race = _make_race(slug="ru2", code="ru2")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Paid", paid_people=2)

    resp = client.get(reverse("all_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-initial="all"' in html
    # both JSON blocks must parse
    teams = _script_json(html, "teams-data")
    cats = _script_json(html, "categories-data")
    assert {t["name"] for t in teams} == {"Paid"}
    assert {c["id"] for c in cats} == {cat.id}


@pytest.mark.django_db
def test_teams2_returns_200_with_category_data_initial(client):
    owner = User.objects.create_user(
        username="ru3", password="p", email="ru3@example.com"
    )
    race = _make_race(slug="ru3", code="ru3")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Paid", paid_people=2)

    resp = client.get(reverse("teams2", args=[race.slug, cat.id]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert f'data-initial="{cat.id}"' in html
    # JSON blocks still parse
    _script_json(html, "teams-data")
    _script_json(html, "categories-data")


@pytest.mark.django_db
def test_my_teams_authenticated_returns_200_with_mine_initial(client):
    owner = User.objects.create_user(
        username="ru4", password="p", email="ru4@example.com"
    )
    race = _make_race(slug="ru4", code="ru4")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Mine", paid_people=2)
    client.force_login(owner)

    resp = client.get(reverse("my_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-initial="mine"' in html


@pytest.mark.django_db
def test_my_teams_anon_redirects_to_login_with_next(client):
    race = _make_race(slug="ru5", code="ru5")
    _make_category(race)

    url = reverse("my_teams", args=[race.slug])
    resp = client.get(url)

    assert resp.status_code == 302
    assert reverse("login") in resp.url
    assert f"next={url}" in resp.url


@pytest.mark.django_db
def test_invalid_race_slug_returns_404(client):
    resp = client.get(reverse("all_teams", args=["does-not-exist"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_invalid_category_returns_404(client):
    race = _make_race(slug="ru6", code="ru6")
    _make_category(race)

    # numeric id that does not exist for this race
    resp = client.get(reverse("teams2", args=[race.slug, 999999]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_non_numeric_category_returns_404(client):
    race = _make_race(slug="ru7", code="ru7")
    _make_category(race)

    resp = client.get(f"/race/{race.slug}/category/not-a-number/teams/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_teams_page_renders_key_markup(client):
    owner = User.objects.create_user(
        username="ru8", password="p", email="ru8@example.com"
    )
    race = _make_race(slug="ru8", code="ru8")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Paid", paid_people=2)

    resp = client.get(reverse("all_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    # page wrapper + initial filter
    assert 'class="teams-page"' in html
    assert 'data-initial="all"' in html
    # cover meta card + breadcrumb
    assert 'class="cover-meta-card"' in html
    assert 'class="crumb"' in html
    # search box + chips container the JS hydrates
    assert 'id="searchInput"' in html
    assert 'id="catChips"' in html
    assert 'class="cat-chips"' in html
    # sortable table + JS-built body / empty state / foot
    assert 'class="teams-table"' in html
    assert 'data-sort="num"' in html
    assert 'data-sort="name"' in html
    assert 'data-sort="cat"' in html
    assert 'data-sort="city"' in html
    assert 'data-sort="cnt"' in html
    assert 'id="teamRows"' in html
    assert 'id="emptyState"' in html
    assert 'id="footCount"' in html
    # sidebar summary + breakdown
    assert 'id="brk"' in html
    assert 'id="resetCat"' in html
    # both JSON blocks present and parseable
    _script_json(html, "teams-data")
    _script_json(html, "categories-data")
    # teams.js wired up
    assert "js/teams.js" in html


@pytest.mark.django_db
def test_embedded_json_escapes_html_specials(client):
    """HTML-special chars in user text must not break out of the block."""
    owner = User.objects.create_user(
        username="ru9", password="p", email="ru9@example.com"
    )
    race = _make_race(slug="ru9", code="ru9")
    cat = _make_category(race)
    # Both a literal </script> and the <!--<script> tokenizer-state vector.
    payload = "</script><!--<script>alert(1)&x"
    _make_team(owner, cat, teamname=payload, paid_people=2)

    resp = client.get(reverse("all_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    # The raw payload must be escaped, so it can neither terminate the data
    # block nor flip the HTML tokenizer into script-data-escaped mode.
    assert payload not in html
    assert "\\u003C/script\\u003E" in html  # </script>
    assert "\\u003C!--\\u003Cscript\\u003E" in html  # <!--<script>
    assert "\\u0026" in html  # &
    # The block still parses and round-trips the original team name.
    teams = _script_json(html, "teams-data")
    assert {t["name"] for t in teams} == {payload}


@pytest.mark.django_db
def test_race_page_anon_sees_login_and_add_button(client):
    """Logged-out visitors get an «add team» CTA that routes through login."""
    race = _make_race(slug="reg-open", code="reg-open")
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["reg_status"])

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Войти и добавить команду" in html
    assert "Добавить команду" not in html.replace("Войти и добавить команду", "")
    # The button points at add_team; the view redirects anon users to login.
    assert reverse("add_team", args=[race.slug]) in html


@pytest.mark.django_db
def test_race_page_authenticated_sees_plain_add_button(client):
    member = User.objects.create_user(
        username="member", password="p", email="member@example.com"
    )
    client.force_login(member)
    race = _make_race(slug="reg-open2", code="reg-open2")
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["reg_status"])

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Добавить команду" in html
    assert "Войти и добавить команду" not in html


@pytest.mark.django_db
def test_race_page_hides_add_button_when_reg_not_open(client):
    race = _make_race(slug="reg-upcoming", code="reg-upcoming")
    # default reg_status is UPCOMING, so no add-team CTA at all.

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Добавить команду" not in html


@pytest.mark.django_db
def test_race_page_admin_sees_edit_button(client):
    user = User.objects.create_user(
        username="raedit", password="p", email="raedit@example.com"
    )
    race = _make_race(slug="edit-btn", code="edit-btn")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    client.force_login(user)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is True
    html = resp.content.decode()
    assert reverse("edit_race", args=[race.slug]) in html
    assert "Редактировать" in html
    # A non-superuser ADMIN does not get the «new race» link.
    assert "+ Новая гонка" not in html


@pytest.mark.django_db
def test_race_page_regular_user_no_edit_button(client):
    user = User.objects.create_user(
        username="plain", password="p", email="plain@example.com"
    )
    race = _make_race(slug="no-edit-btn", code="no-edit-btn")
    client.force_login(user)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is False
    html = resp.content.decode()
    assert "Редактировать" not in html
    assert "+ Новая гонка" not in html


@pytest.mark.django_db
def test_race_page_superuser_sees_edit_and_new_buttons(client):
    admin = User.objects.create_superuser(
        username="su-buttons", password="p", email="su-buttons@example.com"
    )
    race = _make_race(slug="su-btn", code="su-btn")
    client.force_login(admin)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is True
    html = resp.content.decode()
    assert reverse("edit_race", args=[race.slug]) in html
    assert reverse("add_race") in html
    assert "+ Новая гонка" in html


# --- can_edit_race access-control matrix ---


@pytest.mark.django_db
def test_can_edit_race_superuser_true_for_any_race():
    admin = User.objects.create_superuser(
        username="su", password="p", email="su@example.com"
    )
    race = _make_race(slug="ce1", code="ce1")
    other = _make_race(slug="ce1b", code="ce1b")

    assert can_edit_race(admin, race) is True
    assert can_edit_race(admin, other) is True


@pytest.mark.django_db
def test_can_edit_race_admin_only_for_own_race():
    user = User.objects.create_user(username="ra", password="p", email="ra@example.com")
    race = _make_race(slug="ce2", code="ce2")
    other = _make_race(slug="ce2b", code="ce2b")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    assert can_edit_race(user, race) is True
    assert can_edit_race(user, other) is False


@pytest.mark.django_db
def test_can_edit_race_moderator_and_others_false():
    moderator = User.objects.create_user(
        username="mod", password="p", email="mod@example.com"
    )
    regular = User.objects.create_user(
        username="reg", password="p", email="reg@example.com"
    )
    race = _make_race(slug="ce3", code="ce3")
    RaceAdmin.objects.create(race=race, user=moderator, role=RaceAdmin.Role.MODERATOR)

    assert can_edit_race(moderator, race) is False
    assert can_edit_race(regular, race) is False
    assert can_edit_race(AnonymousUser(), race) is False


# --- RaceForm ---


def _race_form_data(**overrides):
    data = {
        "name": "New Race",
        "code": "nr-1",
        "slug": "new-race",
        "place": "Москва",
        "date": "2026-09-01",
        "date_end": "2026-09-02",
        "cost": 1000,
        "header_image": "",
        "header_logo": "",
        "reg_status": RegStatus.UPCOMING,
        "is_active": True,
        "is_legend_visible": False,
        "is_teams_editable": False,
        "is_photo_upload_enabled": False,
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_race_form_valid_data_creates_race():
    form = RaceForm(data=_race_form_data())

    assert form.is_valid(), form.errors
    race = form.save()
    assert Race.objects.filter(pk=race.pk).exists()
    assert race.name == "New Race"
    assert race.code == "nr-1"
    assert race.slug == "new-race"
    assert race.cost == 1000
    assert race.reg_status == RegStatus.UPCOMING


@pytest.mark.django_db
def test_race_form_does_not_include_is_reg_open():
    form = RaceForm()
    assert "is_reg_open" not in form.fields


@pytest.mark.django_db
def test_race_form_duplicate_code_invalid():
    _make_race(slug="existing", code="dup-code")
    form = RaceForm(data=_race_form_data(code="dup-code", slug="fresh-slug"))

    assert not form.is_valid()
    assert "code" in form.errors


@pytest.mark.django_db
def test_race_form_duplicate_slug_invalid():
    _make_race(slug="dup-slug", code="some-code")
    form = RaceForm(data=_race_form_data(code="fresh-code", slug="dup-slug"))

    assert not form.is_valid()
    assert "slug" in form.errors


@pytest.mark.django_db
def test_race_form_edit_keeps_own_code_and_slug_valid():
    race = _make_race(slug="own-slug", code="own-code")
    form = RaceForm(
        data=_race_form_data(code="own-code", slug="own-slug"),
        instance=race,
    )

    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.pk == race.pk
    assert saved.code == "own-code"
    assert saved.slug == "own-slug"


@pytest.mark.django_db
def test_race_form_invalid_header_image_url():
    form = RaceForm(data=_race_form_data(header_image="not-a-url"))

    assert not form.is_valid()
    assert "header_image" in form.errors


# --- RaceEditView GET + auth ---


def _edit_get(path, user, **kwargs):
    request = RequestFactory().get(path)
    request.user = user
    return RaceEditView.as_view()(request, **kwargs)


@pytest.mark.django_db
def test_race_edit_get_anonymous_redirects_to_login():
    race = _make_race(slug="re1", code="re1")

    edit = _edit_get(f"/race/{race.slug}/edit/", AnonymousUser(), race_slug=race.slug)
    create = _edit_get("/races/new/", AnonymousUser())

    for resp in (edit, create):
        assert resp.status_code == 302
        assert reverse("login") in resp.url
        assert "next=" in resp.url
    assert f"next=/race/{race.slug}/edit/" in edit.url


@pytest.mark.django_db
def test_race_edit_get_regular_user_forbidden():
    user = User.objects.create_user(username="re2", password="p", email="re2@e.com")
    race = _make_race(slug="re2", code="re2")

    edit = _edit_get(f"/race/{race.slug}/edit/", user, race_slug=race.slug)
    create = _edit_get("/races/new/", user)

    assert edit.status_code == 403
    assert create.status_code == 403


@pytest.mark.django_db
def test_race_edit_get_moderator_forbidden():
    user = User.objects.create_user(username="re3", password="p", email="re3@e.com")
    race = _make_race(slug="re3", code="re3")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.MODERATOR)

    resp = _edit_get(f"/race/{race.slug}/edit/", user, race_slug=race.slug)

    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_edit_get_superuser_create_returns_200():
    admin = User.objects.create_superuser(
        username="re4", password="p", email="re4@e.com"
    )

    resp = _edit_get("/races/new/", admin)

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Создание гонки" in html
    # empty race has no categories or tiers
    assert _script_json(html, "categories-data") == []
    assert _script_json(html, "price-tiers-data") == []


@pytest.mark.django_db
def test_race_edit_get_admin_edit_returns_200_with_context():
    user = User.objects.create_user(username="re5", password="p", email="re5@e.com")
    race = _make_race(slug="re5", code="re5")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    cat = _make_category(race, code="6h", short_name="6ч", name="6 часов", order=0)
    RacePriceTier.objects.create(race=race, price=1500, active_until="2026-08-01")

    resp = _edit_get(f"/race/{race.slug}/edit/", user, race_slug=race.slug)

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Редактирование гонки" in html
    cats = _script_json(html, "categories-data")
    assert [c["id"] for c in cats] == [cat.id]
    assert cats[0]["min_people"] == 2 and cats[0]["max_people"] == 6
    tiers = _script_json(html, "price-tiers-data")
    assert tiers[0]["price"] == 1500
    assert tiers[0]["active_until"] == "2026-08-01"


@pytest.mark.django_db
def test_race_form_template_renders_fields_and_data(client):
    user = User.objects.create_user(username="tpl", password="p", email="tpl@e.com")
    race = Race.objects.create(
        name="Шаблонная гонка",
        code="tpl-1",
        slug="tpl-1",
        place="Москва",
        cost=0,
        reg_status=RegStatus.OPEN,
    )
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    cat = _make_category(race, code="6h", short_name="6ч", name="6 часов", order=0)
    tier = RacePriceTier.objects.create(
        race=race, price=1500, active_until="2026-08-01"
    )
    client.force_login(user)

    resp = client.get(reverse("edit_race", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    html = resp.content.decode()
    # Scalar fields render their current values into manual inputs.
    assert 'name="name"' in html and 'value="Шаблонная гонка"' in html
    assert 'name="code"' in html and 'value="tpl-1"' in html
    # reg_status select marks the current choice selected.
    assert f'value="{RegStatus.OPEN}" selected' in html
    # cost=0 must survive (not blanked by a `default` filter).
    assert 'name="cost"' in html and 'value="0"' in html
    # The repeaters and hidden serialization inputs the JS hydrates are present.
    assert 'id="categoriesJson"' in html
    assert 'id="priceTiersJson"' in html
    assert 'id="addCat"' in html
    assert 'id="addTier"' in html
    # Both data blocks parse and carry the existing rows.
    cats = _script_json(html, "categories-data")
    assert [c["id"] for c in cats] == [cat.id]
    tiers = _script_json(html, "price-tiers-data")
    assert [t["id"] for t in tiers] == [tier.id]
    assert tiers[0]["active_until"] == "2026-08-01"
    # race_form.js is wired up.
    assert "js/race_form.js" in html


# --- RaceEditView POST save + category/price-tier reconcile ---


def _edit_post(path, user, data, **kwargs):
    request = RequestFactory().post(path, data)
    request.user = user
    return RaceEditView.as_view()(request, **kwargs)


def _post_data(**overrides):
    """Race form fields plus empty category/tier payloads, with overrides."""
    data = _race_form_data()
    data["categories_json"] = "[]"
    data["price_tiers_json"] = "[]"
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_race_edit_post_superuser_create():
    admin = User.objects.create_superuser(
        username="pc1", password="p", email="pc1@e.com"
    )

    resp = _edit_post("/races/new/", admin, _post_data())

    assert resp.status_code == 302
    assert resp.url == reverse("race", kwargs={"race_slug": "new-race"})
    race = Race.objects.get(slug="new-race")
    assert race.name == "New Race"
    assert race.code == "nr-1"
    assert race.cost == 1000
    assert race.reg_status == RegStatus.UPCOMING


@pytest.mark.django_db
def test_race_edit_post_edit_updates_scalar_fields():
    user = User.objects.create_user(username="pe1", password="p", email="pe1@e.com")
    race = _make_race(slug="pe1", code="pe1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    data = _post_data(
        name="Updated Name",
        code="pe1",
        slug="pe1",
        reg_status=RegStatus.OPEN,
        cost=2500,
    )
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.name == "Updated Name"
    assert race.reg_status == RegStatus.OPEN
    assert race.cost == 2500


@pytest.mark.django_db
def test_race_edit_post_category_reconcile_update_create_delete():
    user = User.objects.create_user(username="pc2", password="p", email="pc2@e.com")
    race = _make_race(slug="pc2", code="pc2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    keep = _make_category(race, code="keep", short_name="k", name="Keep", order=0)
    drop = _make_category(race, code="drop", short_name="d", name="Drop", order=1)

    categories = json.dumps(
        [
            {
                "id": keep.id,
                "code": "keep",
                "short_name": "k2",
                "name": "Keep Renamed",
                "description": "",
                "is_active": True,
                "min_people": 3,
                "max_people": 5,
            },
            {
                "id": None,
                "code": "fresh",
                "short_name": "f",
                "name": "Fresh",
                "description": "",
                "is_active": True,
                "min_people": 2,
                "max_people": 4,
            },
        ]
    )
    data = _post_data(code="pc2", slug="pc2", categories_json=categories)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    assert not Category.objects.filter(id=drop.id).exists()
    keep.refresh_from_db()
    assert keep.name == "Keep Renamed"
    assert keep.order == 0
    assert keep.min_people == 3 and keep.max_people == 5
    fresh = Category.objects.get(race=race, code="fresh")
    assert fresh.order == 1
    assert fresh.min_people == 2 and fresh.max_people == 4


@pytest.mark.django_db
def test_race_edit_post_price_tier_reconcile_and_current_price():
    user = User.objects.create_user(username="pt1", password="p", email="pt1@e.com")
    race = _make_race(slug="pt1", code="pt1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    today = datetime.date.today()
    soon = (today + datetime.timedelta(days=30)).isoformat()
    far = (today + datetime.timedelta(days=180)).isoformat()
    keep = RacePriceTier.objects.create(
        race=race,
        price=1500,
        active_until=(today + datetime.timedelta(days=60)).isoformat(),
    )
    drop = RacePriceTier.objects.create(
        race=race,
        price=2000,
        active_until=(today + datetime.timedelta(days=90)).isoformat(),
    )

    tiers = json.dumps(
        [
            {"id": keep.id, "price": 1200, "active_until": soon},
            {"id": None, "price": 1800, "active_until": far},
        ]
    )
    data = _post_data(code="pt1", slug="pt1", price_tiers_json=tiers)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    assert not RacePriceTier.objects.filter(id=drop.id).exists()
    keep.refresh_from_db()
    assert keep.price == 1200
    assert RacePriceTier.objects.filter(race=race, price=1800).exists()
    race.refresh_from_db()
    # The earliest active tier (soonest active_until >= today) has price 1200.
    assert race.current_price == 1200


@pytest.mark.django_db
def test_race_edit_post_cross_race_id_treated_as_new():
    user = User.objects.create_user(username="cr1", password="p", email="cr1@e.com")
    race = _make_race(slug="cr1", code="cr1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    other = _make_race(slug="cr1b", code="cr1b")
    other_cat = _make_category(other, code="oc", short_name="oc", name="Other Cat")
    future = (datetime.date.today() + datetime.timedelta(days=60)).isoformat()
    other_tier = RacePriceTier.objects.create(
        race=other, price=999, active_until=future
    )

    categories = json.dumps(
        [
            {
                "id": other_cat.id,
                "code": "hijack",
                "short_name": "h",
                "name": "Hijack",
                "description": "",
                "is_active": True,
                "min_people": 2,
                "max_people": 6,
            }
        ]
    )
    tiers = json.dumps([{"id": other_tier.id, "price": 111, "active_until": future}])
    data = _post_data(
        code="cr1", slug="cr1", categories_json=categories, price_tiers_json=tiers
    )
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    # The other race's rows are untouched.
    other_cat.refresh_from_db()
    assert other_cat.race_id == other.id and other_cat.code == "oc"
    other_tier.refresh_from_db()
    assert other_tier.race_id == other.id and other_tier.price == 999
    # Our race got a brand-new category/tier instead of hijacking theirs.
    new_cat = Category.objects.get(race=race, code="hijack")
    assert new_cat.id != other_cat.id
    new_tier = RacePriceTier.objects.get(race=race, price=111)
    assert new_tier.id != other_tier.id


@pytest.mark.django_db
def test_race_edit_post_malformed_json_rolls_back():
    user = User.objects.create_user(username="mj1", password="p", email="mj1@e.com")
    race = _make_race(slug="mj1", code="mj1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    data = _post_data(code="mj1", slug="mj1", categories_json="{not json")
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 200
    race.refresh_from_db()
    # The form name "New Race" was NOT applied — full rollback.
    assert race.name == "Teams Race"


@pytest.mark.django_db
def test_race_edit_post_invalid_category_row_rolls_back():
    user = User.objects.create_user(username="iv1", password="p", email="iv1@e.com")
    race = _make_race(slug="iv1", code="iv1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    # Missing code + name.
    bad_missing = json.dumps(
        [{"id": None, "code": "", "name": "", "min_people": 2, "max_people": 6}]
    )
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(code="iv1", slug="iv1", categories_json=bad_missing),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    # min_people > max_people.
    bad_range = json.dumps(
        [{"id": None, "code": "c", "name": "n", "min_people": 5, "max_people": 2}]
    )
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(code="iv1", slug="iv1", categories_json=bad_range),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not Category.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_post_invalid_price_tier_row_rolls_back():
    user = User.objects.create_user(username="iv2", password="p", email="iv2@e.com")
    race = _make_race(slug="iv2", code="iv2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    # Non-positive price.
    bad_price = json.dumps([{"id": None, "price": -5, "active_until": "2026-08-01"}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(code="iv2", slug="iv2", price_tiers_json=bad_price),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    # Bad active_until.
    bad_date = json.dumps([{"id": None, "price": 100, "active_until": "not-a-date"}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(code="iv2", slug="iv2", price_tiers_json=bad_date),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not RacePriceTier.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_post_moderator_other_race_forbidden():
    user = User.objects.create_user(username="pf1", password="p", email="pf1@e.com")
    race = _make_race(slug="pf1", code="pf1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.MODERATOR)

    resp = _edit_post(
        f"/race/{race.slug}/edit/", user, _post_data(), race_slug=race.slug
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_edit_post_admin_create_forbidden():
    # A RaceAdmin(ADMIN) is not a superuser, so they cannot create a race.
    user = User.objects.create_user(username="pf2", password="p", email="pf2@e.com")
    race = _make_race(slug="pf2", code="pf2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    resp = _edit_post("/races/new/", user, _post_data(code="brand-new", slug="bn"))
    assert resp.status_code == 403
    assert not Race.objects.filter(slug="bn").exists()


def test_add_race_and_edit_race_urls_resolve():
    add_url = reverse("add_race")
    assert add_url == "/races/new/"
    assert resolve(add_url).func.view_class is RaceEditView

    edit_url = reverse("edit_race", kwargs={"race_slug": "kolco24-2026"})
    assert edit_url == "/race/kolco24-2026/edit/"
    assert resolve(edit_url).func.view_class is RaceEditView


# --- People-limit config on the race edit page (Task 4) ---


def _cat_row(**overrides):
    """A valid category-row dict for the categories_json payload."""
    row = {
        "id": None,
        "code": "c",
        "short_name": "c",
        "name": "Cat",
        "description": "",
        "is_active": True,
        "min_people": 2,
        "max_people": 6,
        "people_limit": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.django_db
def test_race_form_people_limit_field_present_and_saves():
    form = RaceForm(data=_race_form_data(people_limit=120))

    assert "people_limit" in form.fields
    assert form.is_valid(), form.errors
    race = form.save()
    assert race.people_limit == 120


@pytest.mark.django_db
def test_race_form_people_limit_empty_defaults_to_zero():
    data = _race_form_data()
    data.pop("people_limit", None)
    form = RaceForm(data=data)

    assert form.is_valid(), form.errors
    race = form.save()
    assert race.people_limit == 0


@pytest.mark.django_db
def test_race_form_people_limit_negative_rejected():
    form = RaceForm(data=_race_form_data(people_limit=-5))

    assert not form.is_valid()
    assert "people_limit" in form.errors


@pytest.mark.django_db
def test_race_form_people_limit_zero_accepted():
    form = RaceForm(data=_race_form_data(people_limit=0))

    assert form.is_valid(), form.errors
    race = form.save()
    assert race.people_limit == 0


@pytest.mark.django_db
def test_race_edit_post_saves_race_and_category_people_limit():
    user = User.objects.create_user(username="pl1", password="p", email="pl1@e.com")
    race = _make_race(slug="pl1", code="pl1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    categories = json.dumps([_cat_row(code="six", name="Six", people_limit=40)])
    data = _post_data(
        code="pl1", slug="pl1", people_limit=200, categories_json=categories
    )
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.people_limit == 200
    cat = Category.objects.get(race=race, code="six")
    assert cat.people_limit == 40


@pytest.mark.django_db
def test_race_edit_post_category_people_limit_zero_accepted():
    user = User.objects.create_user(username="pl2", password="p", email="pl2@e.com")
    race = _make_race(slug="pl2", code="pl2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    categories = json.dumps([_cat_row(code="z", name="Zero", people_limit=0)])
    data = _post_data(code="pl2", slug="pl2", categories_json=categories)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    cat = Category.objects.get(race=race, code="z")
    assert cat.people_limit == 0


@pytest.mark.django_db
def test_race_edit_post_category_people_limit_negative_rolls_back():
    user = User.objects.create_user(username="pl3", password="p", email="pl3@e.com")
    race = _make_race(slug="pl3", code="pl3")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    categories = json.dumps([_cat_row(code="neg", name="Neg", people_limit=-1)])
    data = _post_data(code="pl3", slug="pl3", categories_json=categories)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 200
    assert not Category.objects.filter(race=race, code="neg").exists()


@pytest.mark.django_db
def test_race_edit_round_trip_preserves_people_limits():
    user = User.objects.create_user(username="pl4", password="p", email="pl4@e.com")
    race = _make_race(slug="pl4", code="pl4")
    race.people_limit = 150
    race.save(update_fields=["people_limit"])
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    cat = _make_category(race, code="6h", short_name="6ч", name="6 часов", order=0)
    cat.people_limit = 30
    cat.save(update_fields=["people_limit"])

    # GET emits the current limits into the form + category-data island.
    resp = _edit_get(f"/race/{race.slug}/edit/", user, race_slug=race.slug)
    html = resp.content.decode()
    assert 'name="people_limit"' in html and 'value="150"' in html
    cats = _script_json(html, "categories-data")
    assert cats[0]["people_limit"] == 30

    # POST the same limits back (id preserved) and confirm they survive.
    categories = json.dumps(
        [
            _cat_row(
                id=cat.id, code="6h", short_name="6ч", name="6 часов", people_limit=30
            )
        ]
    )
    data = _post_data(
        code="pl4", slug="pl4", people_limit=150, categories_json=categories
    )
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    race.refresh_from_db()
    cat.refresh_from_db()
    assert race.people_limit == 150
    assert cat.people_limit == 30


# --- Task 6: remaining-places badges -----------------------------------------


@pytest.mark.django_db
def test_race_page_context_remaining_with_limit():
    owner = User.objects.create_user(
        username="rp1", password="p", email="rp1@example.com"
    )
    race = _make_race(slug="rem-race", code="rem")
    race.people_limit = 10
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    cat.people_limit = 6
    cat.save(update_fields=["people_limit"])
    _make_team(owner, cat, paid_people=4)

    context = RacePageView.build_context(race)

    # Race: 10 limit − 4 paid = 6 remaining.
    assert context["race_remaining"] == 6
    # Category: 6 limit − 4 paid = 2 remaining (attached to the instance).
    assert context["categories"][0].remaining == 2


@pytest.mark.django_db
def test_race_page_context_remaining_unlimited_is_none():
    race = _make_race(slug="unl-race", code="unl")
    _make_category(race)  # both limits default to 0 → unlimited

    context = RacePageView.build_context(race)

    assert context["race_remaining"] is None
    assert context["categories"][0].remaining is None


@pytest.mark.django_db
def test_race_page_renders_remaining_badge(client):
    owner = User.objects.create_user(
        username="rp2", password="p", email="rp2@example.com"
    )
    race = _make_race(slug="badge-race", code="badge")
    race.people_limit = 5
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=2)

    resp = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()

    assert resp.status_code == 200
    assert "осталось 3 мест" in html


@pytest.mark.django_db
def test_race_page_renders_sold_out_badge(client):
    owner = User.objects.create_user(
        username="rp3", password="p", email="rp3@example.com"
    )
    race = _make_race(slug="full-race", code="full")
    race.people_limit = 2
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=2)

    resp = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()

    assert resp.status_code == 200
    assert "мест нет" in html


@pytest.mark.django_db
def test_race_page_category_card_shows_labelled_stats(client):
    """Category card states teams and participants in their own units.

    The race itself is unlimited (no cover-line badge), so «осталось K из L»
    can only come from the category row — it must read participants, not teams.
    """
    owner = User.objects.create_user(
        username="rp5", password="p", email="rp5@example.com"
    )
    race = _make_race(slug="stats-race", code="stats")  # race unlimited
    cat = _make_category(race)
    cat.people_limit = 10
    cat.save(update_fields=["people_limit"])
    _make_team(owner, cat, paid_people=2, start_number="1")
    _make_team(owner, cat, paid_people=3, start_number="2")

    context = RacePageView.build_context(race)
    assert context["categories"][0].team_count == 2
    assert context["categories"][0].people == 5
    assert context["categories"][0].remaining == 5  # 10 − 5

    resp = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()

    assert resp.status_code == 200
    assert "<b>2</b> команд" in html
    assert "<b>5</b> участников" in html
    assert "осталось 5 из 10" in html


@pytest.mark.django_db
def test_race_page_category_card_unlimited_hides_remaining(client):
    owner = User.objects.create_user(
        username="rp6", password="p", email="rp6@example.com"
    )
    race = _make_race(slug="stats-unl", code="statsunl")
    cat = _make_category(race)  # people_limit defaults to 0 → unlimited
    _make_team(owner, cat, paid_people=2, start_number="1")

    context = RacePageView.build_context(race)
    assert context["categories"][0].remaining is None

    resp = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()

    assert resp.status_code == 200
    assert "<b>1</b> команд" in html
    assert "<b>2</b> участников" in html
    # No limit → no «осталось …» line in this category row.
    assert "осталось" not in html


@pytest.mark.django_db
def test_teams_context_includes_race_remaining():
    owner = User.objects.create_user(
        username="rp4", password="p", email="rp4@example.com"
    )
    race = _make_race(slug="tr-rem", code="trrem")
    race.people_limit = 8
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=3)

    context = RaceTeamsView.build_context(race, AnonymousUser())

    assert context["race_remaining"] == 5
