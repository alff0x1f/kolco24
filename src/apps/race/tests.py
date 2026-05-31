import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.urls import resolve, reverse

from apps.race.forms import RaceForm
from apps.race.views import RaceEditView, RaceTeamsView
from website.models import Race
from website.models.models import Team
from website.models.race import Category, RaceAdmin, RacePriceTier, RegStatus
from website.views.views_ import can_edit_race


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
