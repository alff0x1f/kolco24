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


def _make_race(slug="teams-race"):
    return Race.objects.create(name="Teams Race", slug=slug)


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
def test_build_context_reg_status_flags():
    race = _make_race()  # defaults to RegStatus.UPCOMING

    ctx = RaceTeamsView.build_context(race, AnonymousUser())
    assert ctx["reg_open"] is False
    assert ctx["reg_upcoming"] is True

    race.reg_status = RegStatus.OPEN
    race.save()
    ctx = RaceTeamsView.build_context(race, AnonymousUser())
    assert ctx["reg_open"] is True
    assert ctx["reg_upcoming"] is False

    race.reg_status = RegStatus.SOLD_OUT
    race.save()
    ctx = RaceTeamsView.build_context(race, AnonymousUser())
    assert ctx["reg_open"] is False
    assert ctx["reg_upcoming"] is False


@pytest.mark.django_db
def test_build_context_can_edit_race_flag():
    race = _make_race()

    anon_ctx = RaceTeamsView.build_context(race, AnonymousUser())
    assert anon_ctx["can_edit_race"] is False

    plain = User.objects.create_user(
        username="plain", password="p", email="plain@example.com"
    )
    assert RaceTeamsView.build_context(race, plain)["can_edit_race"] is False

    admin = User.objects.create_user(
        username="radmin", password="p", email="radmin@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    assert RaceTeamsView.build_context(race, admin)["can_edit_race"] is True

    superuser = User.objects.create_superuser(
        username="su", password="p", email="su@example.com"
    )
    assert RaceTeamsView.build_context(race, superuser)["can_edit_race"] is True


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
    race = _make_race(slug="r2")
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
    race = _make_race(slug="r3")
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
    race = _make_race(slug="r4")
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
    race = _make_race(slug="r5")
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
    race = _make_race(slug="r6")
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
    race = _make_race(slug="r7")
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
    race = _make_race(slug="r8")
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
    race = _make_race(slug="r9")
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
    race = _make_race(slug="r10")
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
    race = _make_race(slug="ru1")
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
    race = _make_race(slug="ru2")
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
def test_all_teams_renders_admin_panel_for_superuser(client, django_user_model):
    race = _make_race(slug="ru2a")
    superuser = django_user_model.objects.create_superuser(
        username="su2", password="p", email="su2@example.com"
    )
    client.force_login(superuser)

    resp = client.get(reverse("all_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "card-admin" in html
    # «Редактировать гонку» lives on the race page now, not on teams.
    assert reverse("edit_race", args=[race.slug]) not in html
    # The teams admin panel keeps «+ Команда».
    assert reverse("add_team", args=[race.slug]) in html


@pytest.mark.django_db
def test_teams2_returns_200_with_category_data_initial(client):
    owner = User.objects.create_user(
        username="ru3", password="p", email="ru3@example.com"
    )
    race = _make_race(slug="ru3")
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
    race = _make_race(slug="ru4")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Mine", paid_people=2)
    client.force_login(owner)

    resp = client.get(reverse("my_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-initial="mine"' in html


@pytest.mark.django_db
def test_my_teams_anon_redirects_to_login_with_next(client):
    race = _make_race(slug="ru5")
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
    race = _make_race(slug="ru6")
    _make_category(race)

    # numeric id that does not exist for this race
    resp = client.get(reverse("teams2", args=[race.slug, 999999]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_non_numeric_category_returns_404(client):
    race = _make_race(slug="ru7")
    _make_category(race)

    resp = client.get(f"/race/{race.slug}/category/not-a-number/teams/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_teams_page_renders_key_markup(client):
    owner = User.objects.create_user(
        username="ru8", password="p", email="ru8@example.com"
    )
    race = _make_race(slug="ru8")
    cat = _make_category(race)
    _make_team(owner, cat, teamname="Paid", paid_people=2)

    resp = client.get(reverse("all_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    # page wrapper + initial filter
    assert 'class="teams-page"' in html
    assert 'data-initial="all"' in html
    # cover meta card + breadcrumb trail (race › Команды), leaf is the <h1>
    assert 'class="cover-meta-card"' in html
    assert "cover-crumbs" in html
    assert reverse("race", args=[race.slug]) in html
    assert '<h1 aria-current="page">Команды</h1>' in html
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
    race = _make_race(slug="ru9")
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
    race = _make_race(slug="reg-open")
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["reg_status"])

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Зарегистрировать команду" in html
    assert "Добавить команду" not in html
    # The button points at add_team; the view routes anon users through the
    # passwordless account_start flow (not password login).
    assert reverse("add_team", args=[race.slug]) in html


@pytest.mark.django_db
def test_race_page_authenticated_sees_plain_add_button(client):
    member = User.objects.create_user(
        username="member", password="p", email="member@example.com"
    )
    client.force_login(member)
    race = _make_race(slug="reg-open2")
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["reg_status"])

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Добавить команду" in html
    assert "Зарегистрировать команду" not in html


@pytest.mark.django_db
def test_race_page_hides_add_button_when_reg_not_open(client):
    race = _make_race(slug="reg-upcoming")
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
    race = _make_race(slug="edit-btn")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    client.force_login(user)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is True
    html = resp.content.decode()
    assert "card-admin" in html  # «Управление» panel in the sidebar
    assert reverse("edit_race", args=[race.slug]) in html
    assert "Редактировать" in html
    # A non-superuser ADMIN does not get the «new race» link.
    assert "+ Новая гонка" not in html


@pytest.mark.django_db
def test_race_page_regular_user_no_edit_button(client):
    user = User.objects.create_user(
        username="plain", password="p", email="plain@example.com"
    )
    race = _make_race(slug="no-edit-btn")
    client.force_login(user)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is False
    html = resp.content.decode()
    assert "card-admin" not in html  # no «Управление» panel for regular users
    assert "Редактировать" not in html
    assert "+ Новая гонка" not in html


@pytest.mark.django_db
def test_race_page_superuser_sees_edit_and_new_buttons(client):
    admin = User.objects.create_superuser(
        username="su-buttons", password="p", email="su-buttons@example.com"
    )
    race = _make_race(slug="su-btn")
    client.force_login(admin)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is True
    html = resp.content.decode()
    assert "card-admin" in html  # «Управление» panel in the sidebar
    assert reverse("edit_race", args=[race.slug]) in html
    assert reverse("add_race") in html
    assert "+ Новая гонка" in html


# --- can_edit_race access-control matrix ---


@pytest.mark.django_db
def test_can_edit_race_superuser_true_for_any_race():
    admin = User.objects.create_superuser(
        username="su", password="p", email="su@example.com"
    )
    race = _make_race(slug="ce1")
    other = _make_race(slug="ce1b")

    assert can_edit_race(admin, race) is True
    assert can_edit_race(admin, other) is True


@pytest.mark.django_db
def test_can_edit_race_admin_only_for_own_race():
    user = User.objects.create_user(username="ra", password="p", email="ra@example.com")
    race = _make_race(slug="ce2")
    other = _make_race(slug="ce2b")
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
    race = _make_race(slug="ce3")
    RaceAdmin.objects.create(race=race, user=moderator, role=RaceAdmin.Role.MODERATOR)

    assert can_edit_race(moderator, race) is False
    assert can_edit_race(regular, race) is False
    assert can_edit_race(AnonymousUser(), race) is False


# --- RaceForm ---


def _race_form_data(**overrides):
    data = {
        "name": "New Race",
        "slug": "new-race",
        "place": "Москва",
        "date": "2026-09-01",
        "date_end": "2026-09-02",
        "cost": 1000,
        "header_image": "",
        "header_logo": "",
        "reg_status": RegStatus.UPCOMING,
        "is_published": True,
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
    assert race.slug == "new-race"
    assert race.cost == 1000
    assert race.reg_status == RegStatus.UPCOMING


@pytest.mark.django_db
def test_race_form_does_not_include_is_reg_open():
    form = RaceForm()
    assert "is_reg_open" not in form.fields


@pytest.mark.django_db
def test_race_form_does_not_include_is_legend_visible():
    form = RaceForm()
    assert "is_legend_visible" not in form.fields
    # form still saves cleanly without the removed field
    save_form = RaceForm(data=_race_form_data())
    assert save_form.is_valid(), save_form.errors
    save_form.save()


@pytest.mark.django_db
def test_race_form_duplicate_slug_invalid():
    _make_race(slug="dup-slug")
    form = RaceForm(data=_race_form_data(slug="dup-slug"))

    assert not form.is_valid()
    assert "slug" in form.errors


@pytest.mark.django_db
def test_race_form_edit_keeps_own_slug_valid():
    race = _make_race(slug="own-slug")
    form = RaceForm(
        data=_race_form_data(slug="own-slug"),
        instance=race,
    )

    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.pk == race.pk
    assert saved.slug == "own-slug"


@pytest.mark.django_db
def test_race_form_invalid_header_image_url():
    form = RaceForm(data=_race_form_data(header_image="not-a-url"))

    assert not form.is_valid()
    assert "header_image" in form.errors


@pytest.mark.django_db
def test_race_form_relative_header_paths_valid():
    form = RaceForm(
        data=_race_form_data(
            header_image="/static/images/backgrounds/header2023.jpg",
            header_logo="/static/images/logo_big.png",
        )
    )

    assert form.is_valid(), form.errors
    race = form.save()
    assert race.header_image == "/static/images/backgrounds/header2023.jpg"
    assert race.header_logo == "/static/images/logo_big.png"


# --- RaceEditView GET + auth ---


def _edit_get(path, user, **kwargs):
    request = RequestFactory().get(path)
    request.user = user
    return RaceEditView.as_view()(request, **kwargs)


@pytest.mark.django_db
def test_race_edit_get_anonymous_redirects_to_login():
    race = _make_race(slug="re1")

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
    race = _make_race(slug="re2")

    edit = _edit_get(f"/race/{race.slug}/edit/", user, race_slug=race.slug)
    create = _edit_get("/races/new/", user)

    assert edit.status_code == 403
    assert create.status_code == 403


@pytest.mark.django_db
def test_race_edit_get_moderator_forbidden():
    user = User.objects.create_user(username="re3", password="p", email="re3@e.com")
    race = _make_race(slug="re3")
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
    race = _make_race(slug="re5")
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
    # The removed legend-visibility toggle leaves no trace in the form.
    assert "is_legend_visible" not in html
    # Scalar fields render their current values into manual inputs.
    assert 'name="name"' in html and 'value="Шаблонная гонка"' in html
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
    # is_published toggle uses the renamed field name.
    assert 'name="is_published"' in html
    assert 'name="is_active"' not in html
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
    assert race.cost == 1000
    assert race.reg_status == RegStatus.UPCOMING
    assert race.is_published is True


@pytest.mark.django_db
def test_race_edit_post_edit_updates_scalar_fields():
    user = User.objects.create_user(username="pe1", password="p", email="pe1@e.com")
    race = _make_race(slug="pe1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    data = _post_data(
        name="Updated Name",
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
    race = _make_race(slug="pc2")
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
    data = _post_data(slug="pc2", categories_json=categories)
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
    race = _make_race(slug="pt1")
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
    data = _post_data(slug="pt1", price_tiers_json=tiers)
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
    race = _make_race(slug="cr1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    other = _make_race(slug="cr1b")
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
    data = _post_data(slug="cr1", categories_json=categories, price_tiers_json=tiers)
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
    race = _make_race(slug="mj1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    data = _post_data(slug="mj1", categories_json="{not json")
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 200
    race.refresh_from_db()
    # The form name "New Race" was NOT applied — full rollback.
    assert race.name == "Teams Race"


@pytest.mark.django_db
def test_race_edit_post_invalid_category_row_rolls_back():
    user = User.objects.create_user(username="iv1", password="p", email="iv1@e.com")
    race = _make_race(slug="iv1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    # Missing code + name.
    bad_missing = json.dumps(
        [{"id": None, "code": "", "name": "", "min_people": 2, "max_people": 6}]
    )
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="iv1", categories_json=bad_missing),
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
        _post_data(slug="iv1", categories_json=bad_range),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not Category.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_post_invalid_price_tier_row_rolls_back():
    user = User.objects.create_user(username="iv2", password="p", email="iv2@e.com")
    race = _make_race(slug="iv2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    # Non-positive price.
    bad_price = json.dumps([{"id": None, "price": -5, "active_until": "2026-08-01"}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="iv2", price_tiers_json=bad_price),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    # Bad active_until.
    bad_date = json.dumps([{"id": None, "price": 100, "active_until": "not-a-date"}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="iv2", price_tiers_json=bad_date),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not RacePriceTier.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_post_moderator_other_race_forbidden():
    user = User.objects.create_user(username="pf1", password="p", email="pf1@e.com")
    race = _make_race(slug="pf1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.MODERATOR)

    resp = _edit_post(
        f"/race/{race.slug}/edit/", user, _post_data(), race_slug=race.slug
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_edit_post_admin_create_forbidden():
    # A RaceAdmin(ADMIN) is not a superuser, so they cannot create a race.
    user = User.objects.create_user(username="pf2", password="p", email="pf2@e.com")
    race = _make_race(slug="pf2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    resp = _edit_post("/races/new/", user, _post_data(slug="bn"))
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
    race = _make_race(slug="pl1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    categories = json.dumps([_cat_row(code="six", name="Six", people_limit=40)])
    data = _post_data(slug="pl1", people_limit=200, categories_json=categories)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.people_limit == 200
    cat = Category.objects.get(race=race, code="six")
    assert cat.people_limit == 40


@pytest.mark.django_db
def test_race_edit_post_category_people_limit_zero_accepted():
    user = User.objects.create_user(username="pl2", password="p", email="pl2@e.com")
    race = _make_race(slug="pl2")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    categories = json.dumps([_cat_row(code="z", name="Zero", people_limit=0)])
    data = _post_data(slug="pl2", categories_json=categories)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    cat = Category.objects.get(race=race, code="z")
    assert cat.people_limit == 0


@pytest.mark.django_db
def test_race_edit_post_category_people_limit_negative_rolls_back():
    user = User.objects.create_user(username="pl3", password="p", email="pl3@e.com")
    race = _make_race(slug="pl3")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    categories = json.dumps([_cat_row(code="neg", name="Neg", people_limit=-1)])
    data = _post_data(slug="pl3", categories_json=categories)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 200
    assert not Category.objects.filter(race=race, code="neg").exists()


@pytest.mark.django_db
def test_race_edit_round_trip_preserves_people_limits():
    user = User.objects.create_user(username="pl4", password="p", email="pl4@e.com")
    race = _make_race(slug="pl4")
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
    data = _post_data(slug="pl4", people_limit=150, categories_json=categories)
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
    race = _make_race(slug="rem-race")
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
    race = _make_race(slug="unl-race")
    _make_category(race)  # both limits default to 0 → unlimited

    context = RacePageView.build_context(race)

    assert context["race_remaining"] is None
    assert context["categories"][0].remaining is None


@pytest.mark.django_db
def test_race_page_renders_remaining_badge(client):
    owner = User.objects.create_user(
        username="rp2", password="p", email="rp2@example.com"
    )
    race = _make_race(slug="badge-race")
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
    race = _make_race(slug="full-race")
    race.people_limit = 2
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=2)

    resp = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()

    assert resp.status_code == 200
    assert "мест нет" in html


@pytest.mark.django_db
def test_race_page_category_badge_sold_out_when_race_full():
    """Race-level limit reached → every category badge reads «мест нет».

    The category still has free slots within its own limit, but the race is
    full, so registration is impossible — the per-category badge must mirror
    that (``remaining`` forced to 0), not show «осталось K из L».
    """
    owner = User.objects.create_user(
        username="rp4", password="p", email="rp4@example.com"
    )
    race = _make_race(slug="racefull-cat")
    race.people_limit = 4
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    cat.people_limit = 20  # plenty of room in the category itself
    cat.save(update_fields=["people_limit"])
    _make_team(owner, cat, paid_people=4)  # fills the race

    context = RacePageView.build_context(race)

    assert context["race_remaining"] == 0
    # Category alone would have 16 free, but the race is full → forced to 0.
    assert context["categories"][0].remaining == 0


@pytest.mark.django_db
def test_race_page_category_card_shows_labelled_stats(client):
    """Category card states teams and participants in their own units.

    The race itself is unlimited (no cover-line badge), so «осталось K из L»
    can only come from the category row — it must read participants, not teams.
    """
    owner = User.objects.create_user(
        username="rp5", password="p", email="rp5@example.com"
    )
    race = _make_race(slug="stats-race")  # race unlimited
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
    # 2 → few form «команды», 5 → many form «участников».
    assert "<b>2</b> команды" in html
    assert "<b>5</b> участников" in html
    assert "осталось 5 из 10" in html


@pytest.mark.django_db
def test_race_page_category_card_unlimited_hides_remaining(client):
    owner = User.objects.create_user(
        username="rp6", password="p", email="rp6@example.com"
    )
    race = _make_race(slug="stats-unl")
    cat = _make_category(race)  # people_limit defaults to 0 → unlimited
    _make_team(owner, cat, paid_people=2, start_number="1")

    context = RacePageView.build_context(race)
    assert context["categories"][0].remaining is None

    resp = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()

    assert resp.status_code == 200
    # 1 → one form «команда», 2 → few form «участника».
    assert "<b>1</b> команда" in html
    assert "<b>2</b> участника" in html
    # No limit → no «осталось …» line in this category row.
    assert "осталось" not in html


@pytest.mark.django_db
def test_teams_context_includes_race_remaining():
    owner = User.objects.create_user(
        username="rp4", password="p", email="rp4@example.com"
    )
    race = _make_race(slug="tr-rem")
    race.people_limit = 8
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=3)

    context = RaceTeamsView.build_context(race, AnonymousUser())

    assert context["race_remaining"] == 5


# --- "мест нет": hide the add-team CTA when the race is full ------------------


@pytest.mark.django_db
def test_build_context_exposes_race_full_flag():
    owner = User.objects.create_user(
        username="rf1", password="p", email="rf1@example.com"
    )
    race = _make_race(slug="rf-ctx")
    race.people_limit = 4
    race.save(update_fields=["people_limit"])
    cat = _make_category(race)

    # Room left → not full (both views agree).
    _make_team(owner, cat, paid_people=2)
    assert RacePageView.build_context(race)["race_full"] is False
    assert RaceTeamsView.build_context(race, AnonymousUser())["race_full"] is False

    # Race cap reached → full.
    _make_team(owner, cat, paid_people=2, start_number="2")
    assert RacePageView.build_context(race)["race_full"] is True
    assert RaceTeamsView.build_context(race, AnonymousUser())["race_full"] is True


@pytest.mark.django_db
def test_race_full_is_false_when_unlimited():
    """No ``people_limit`` (0) → ``remaining_people()`` is None → never full."""
    owner = User.objects.create_user(
        username="rf2", password="p", email="rf2@example.com"
    )
    race = _make_race(slug="rf-unl")  # people_limit defaults to 0
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=2)

    assert RacePageView.build_context(race)["race_full"] is False
    assert RaceTeamsView.build_context(race, AnonymousUser())["race_full"] is False


@pytest.mark.django_db
def test_race_page_hides_add_team_cta_when_full(client):
    owner = User.objects.create_user(
        username="rf3", password="p", email="rf3@example.com"
    )
    race = _make_race(slug="rf-hide")
    race.people_limit = 4
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["people_limit", "reg_status"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=2)

    add_url = reverse("add_team", args=[race.slug])
    page_url = reverse("race", kwargs={"race_slug": race.slug})

    # Room left + reg open → public CTAs link to add_team.
    html = client.get(page_url).content.decode()
    assert add_url in html

    # Fill the race → CTAs gone, «мест нет» badge shown instead.
    _make_team(owner, cat, paid_people=2, start_number="2")
    html = client.get(page_url).content.decode()
    assert add_url not in html
    assert "мест нет" in html


@pytest.mark.django_db
def test_teams_page_hides_add_team_button_when_full(client):
    owner = User.objects.create_user(
        username="rf4", password="p", email="rf4@example.com"
    )
    race = _make_race(slug="rf-teams-hide")
    race.people_limit = 4
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["people_limit", "reg_status"])
    cat = _make_category(race)
    _make_team(owner, cat, paid_people=2)

    # A plain authenticated user (not a race admin) sees the toolbar button.
    viewer = User.objects.create_user(
        username="rf4v", password="p", email="rf4v@example.com"
    )
    client.force_login(viewer)
    add_url = reverse("add_team", args=[race.slug])
    teams_url = reverse("all_teams", args=[race.slug])

    html = client.get(teams_url).content.decode()
    assert add_url in html

    _make_team(owner, cat, paid_people=2, start_number="2")
    html = client.get(teams_url).content.decode()
    assert add_url not in html


# --- Add-on models (RaceExtra / TeamExtra / PaymentExtra) ---

import pytest as _pytest  # noqa: E402
from django.db import IntegrityError  # noqa: E402
from django.db.models import ProtectedError  # noqa: E402

from apps.race.models import PaymentExtra, RaceExtra, TeamExtra  # noqa: E402
from website.models.models import Payment  # noqa: E402


@pytest.mark.django_db
def test_race_extra_create_and_str():
    race = _make_race(slug="ex-race")
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, free_per_team=0
    )
    assert extra.is_active is True
    assert extra.order == 0
    assert "Трансфер" in str(extra)
    assert "transfer" in str(extra)


@pytest.mark.django_db
def test_race_extra_unique_together():
    race = _make_race(slug="ex-uniq")
    RaceExtra.objects.create(race=race, code="map", name="Карты", price=200)
    with _pytest.raises(IntegrityError):
        RaceExtra.objects.create(race=race, code="map", name="Карты 2", price=300)


@pytest.mark.django_db
def test_race_extra_default_ordering():
    race = _make_race(slug="ex-ord")
    RaceExtra.objects.create(race=race, code="b", name="B", order=2)
    RaceExtra.objects.create(race=race, code="a", name="A", order=1)
    RaceExtra.objects.create(race=race, code="c", name="C", order=0)
    codes = list(race.extras.values_list("code", flat=True))
    assert codes == ["c", "a", "b"]


@pytest.mark.django_db
def test_team_extra_create_and_unique_together():
    owner = User.objects.create_user(
        username="te1", password="p", email="te1@example.com"
    )
    race = _make_race(slug="te-race")
    cat = _make_category(race)
    team = _make_team(owner, cat)
    extra = RaceExtra.objects.create(race=race, code="map", name="Карты", price=200)
    te = TeamExtra.objects.create(team=team, race_extra=extra, count=3, count_paid=1)
    assert str(te)
    with _pytest.raises(IntegrityError):
        TeamExtra.objects.create(team=team, race_extra=extra, count=1)


@pytest.mark.django_db
def test_team_extra_protect_blocks_race_extra_delete():
    owner = User.objects.create_user(
        username="te2", password="p", email="te2@example.com"
    )
    race = _make_race(slug="te-prot")
    cat = _make_category(race)
    team = _make_team(owner, cat)
    extra = RaceExtra.objects.create(race=race, code="map", name="Карты", price=200)
    TeamExtra.objects.create(team=team, race_extra=extra, count=2)
    with _pytest.raises(ProtectedError):
        extra.delete()


@pytest.mark.django_db
def test_payment_extra_create_and_str():
    owner = User.objects.create_user(
        username="pe1", password="p", email="pe1@example.com"
    )
    race = _make_race(slug="pe-race")
    cat = _make_category(race)
    team = _make_team(owner, cat)
    extra = RaceExtra.objects.create(race=race, code="map", name="Карты", price=200)
    payment = Payment.objects.create(owner=owner, team=team, payment_method="sbp2")
    pe = PaymentExtra.objects.create(
        payment=payment, race_extra=extra, count=2, unit_price=200
    )
    assert str(pe)
    assert pe.unit_price == 200


@pytest.mark.django_db
def test_payment_extra_protect_blocks_race_extra_delete():
    owner = User.objects.create_user(
        username="pe2", password="p", email="pe2@example.com"
    )
    race = _make_race(slug="pe-prot")
    cat = _make_category(race)
    team = _make_team(owner, cat)
    extra = RaceExtra.objects.create(race=race, code="map", name="Карты", price=200)
    payment = Payment.objects.create(owner=owner, team=team, payment_method="sbp2")
    PaymentExtra.objects.create(payment=payment, race_extra=extra, count=2)
    with _pytest.raises(ProtectedError):
        extra.delete()


# --- Data migration: maps → extras (0002) ---

import importlib  # noqa: E402

from django.apps import apps as _django_apps  # noqa: E402

_maps_mig = importlib.import_module("apps.race.migrations.0002_migrate_maps_to_extras")


def _run_maps_forward():
    _maps_mig.forward(_django_apps, None)


def _run_maps_reverse():
    _maps_mig.reverse(_django_apps, None)


@pytest.mark.django_db
def test_maps_migration_backfills_race_team_payment_extras():
    owner = User.objects.create_user(
        username="mm1", password="p", email="mm1@example.com"
    )
    race = _make_race(slug="mm-race")
    cat = _make_category(race)
    team = _make_team(owner, cat, map_count=3, map_count_paid=1)
    payment = Payment.objects.create(
        owner=owner, team=team, payment_method="sbp2", map=2
    )

    _run_maps_forward()

    extra = RaceExtra.objects.get(race=race, code="map")
    assert extra.name == "Доп. карты"
    assert extra.price == 200
    assert extra.free_per_team == 2
    assert extra.is_active is True

    te = TeamExtra.objects.get(team=team, race_extra=extra)
    assert te.count == 3
    assert te.count_paid == 1

    pe = PaymentExtra.objects.get(payment=payment, race_extra=extra)
    assert pe.count == 2
    assert pe.unit_price == 200


@pytest.mark.django_db
def test_maps_migration_skips_team_without_category():
    owner = User.objects.create_user(
        username="mm2", password="p", email="mm2@example.com"
    )
    # category2 is nullable; build a team with maps but no category.
    team = Team.objects.create(
        owner=owner,
        category2=None,
        paid_people=2,
        ucount=2,
        start_number="1",
        map_count=4,
        map_count_paid=0,
    )

    # Must not raise even though the team has no resolvable race.
    _run_maps_forward()

    assert not TeamExtra.objects.filter(team=team).exists()


@pytest.mark.django_db
def test_maps_migration_skips_payment_without_team():
    owner = User.objects.create_user(
        username="mm3", password="p", email="mm3@example.com"
    )
    payment = Payment.objects.create(
        owner=owner, team=None, payment_method="sbp2", map=2
    )

    # Must not raise even though the payment has no team/category2.
    _run_maps_forward()

    assert not PaymentExtra.objects.filter(payment=payment).exists()


@pytest.mark.django_db
def test_maps_migration_reuses_existing_map_extra_without_error():
    owner = User.objects.create_user(
        username="mm4", password="p", email="mm4@example.com"
    )
    race = _make_race(slug="mm-pre")
    cat = _make_category(race)
    team = _make_team(owner, cat, map_count=2, map_count_paid=0)
    # A pre-existing map extra with a *custom* price must be reused, not
    # overwritten by the migration defaults, and must not raise IntegrityError.
    existing = RaceExtra.objects.create(
        race=race, code="map", name="Свои карты", price=300, free_per_team=1
    )

    _run_maps_forward()

    assert RaceExtra.objects.filter(race=race, code="map").count() == 1
    existing.refresh_from_db()
    assert existing.price == 300
    assert existing.name == "Свои карты"
    te = TeamExtra.objects.get(team=team)
    assert te.race_extra_id == existing.id


@pytest.mark.django_db
def test_maps_migration_reverse_removes_map_rows():
    owner = User.objects.create_user(
        username="mm5", password="p", email="mm5@example.com"
    )
    race = _make_race(slug="mm-rev")
    cat = _make_category(race)
    team = _make_team(owner, cat, map_count=3, map_count_paid=1)
    payment = Payment.objects.create(
        owner=owner, team=team, payment_method="sbp2", map=2
    )

    _run_maps_forward()
    assert RaceExtra.objects.filter(code="map").exists()
    assert TeamExtra.objects.exists()
    assert PaymentExtra.objects.exists()

    _run_maps_reverse()

    assert not RaceExtra.objects.filter(code="map").exists()
    assert not TeamExtra.objects.filter(team=team).exists()
    assert not PaymentExtra.objects.filter(payment=payment).exists()
    # Legacy columns remain untouched, so the data is recoverable.
    team.refresh_from_db()
    payment.refresh_from_db()
    assert team.map_count == 3
    assert payment.map == 2


# --- Pricing helpers (compute_team_charge / upsert_team_extras / payment) ---

from apps.race.pricing import (  # noqa: E402
    ExtraCharge,
    compute_team_charge,
    create_team_payment,
    upsert_team_extras,
)


def _priced_team(username, *, cost=1000, ucount=3, paid_people=1, slug=None):
    """Owner + race (with a flat fee) + category + team, for charge tests."""
    owner = User.objects.create_user(
        username=username, password="p", email=f"{username}@example.com"
    )
    slug = slug or f"pr-{username}"
    race = _make_race(slug=slug)
    race.cost = cost
    race.save(update_fields=["cost"])
    cat = _make_category(race)
    team = _make_team(owner, cat, ucount=ucount, paid_people=paid_people)
    return owner, race, team


@pytest.mark.django_db
def test_compute_team_charge_fee_only():
    _, race, team = _priced_team("ch1", cost=1000, ucount=3, paid_people=1)

    total, lines = compute_team_charge(team, race)

    # (3 − 1) × 1000, no extras.
    assert total == 2000
    assert lines == []


@pytest.mark.django_db
def test_compute_team_charge_single_extra():
    _, race, team = _priced_team("ch2", cost=1000, ucount=3, paid_people=1)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=2, count_paid=0)

    total, lines = compute_team_charge(team, race)

    # fee 2000 + 2 × 500 = 3000.
    assert total == 3000
    assert lines == [ExtraCharge(race_extra=transfer, count=2, unit_price=500)]


@pytest.mark.django_db
def test_compute_team_charge_multiple_extras_summed():
    _, race, team = _priced_team("ch3", cost=1000, ucount=4, paid_people=2)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, order=0
    )
    maps = RaceExtra.objects.create(
        race=race, code="map", name="Карты", price=200, free_per_team=2, order=1
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=1)
    TeamExtra.objects.create(team=team, race_extra=maps, count=2)

    total, lines = compute_team_charge(team, race)

    # fee (4−2)×1000 + transfer 1×500 + maps 2×200 = 2000 + 500 + 400.
    assert total == 2900
    assert [line.race_extra for line in lines] == [transfer, maps]
    assert [line.count for line in lines] == [1, 2]


@pytest.mark.django_db
def test_compute_team_charge_no_delta_when_fully_paid():
    _, race, team = _priced_team("ch4", cost=1000, ucount=2, paid_people=2)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=2, count_paid=2)

    total, lines = compute_team_charge(team, race)

    # Fully paid people and extra → nothing to charge.
    assert total == 0
    assert lines == []


@pytest.mark.django_db
def test_compute_team_charge_partial_extra_delta():
    _, race, team = _priced_team("ch5", cost=1000, ucount=2, paid_people=2)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    # Wants 3, already paid for 1 → charge the 2-unit delta only.
    TeamExtra.objects.create(team=team, race_extra=transfer, count=3, count_paid=1)

    total, lines = compute_team_charge(team, race)

    assert total == 1000  # 2 × 500
    assert lines == [ExtraCharge(race_extra=transfer, count=2, unit_price=500)]


@pytest.mark.django_db
def test_compute_team_charge_floors_at_zero():
    # Over-paid people (refund-like) must not produce a negative total.
    _, race, team = _priced_team("ch6", cost=1000, ucount=1, paid_people=3)

    total, lines = compute_team_charge(team, race)

    assert total == 0
    assert lines == []


@pytest.mark.django_db
def test_compute_team_charge_ignores_inactive_extra():
    _, race, team = _priced_team("ch7", cost=1000, ucount=2, paid_people=2)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, is_active=False
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=2, count_paid=0)

    total, lines = compute_team_charge(team, race)

    assert total == 0
    assert lines == []


@pytest.mark.django_db
def test_upsert_team_extras_creates_then_updates():
    _, race, team = _priced_team("up1", cost=1000)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )

    upsert_team_extras(team, {"extra_transfer": 2}, race)
    te = TeamExtra.objects.get(team=team, race_extra=transfer)
    assert te.count == 2
    assert te.count_paid == 0

    # Second call updates the same row, not a duplicate.
    upsert_team_extras(team, {"extra_transfer": 5}, race)
    assert TeamExtra.objects.filter(team=team, race_extra=transfer).count() == 1
    te.refresh_from_db()
    assert te.count == 5


@pytest.mark.django_db
def test_upsert_team_extras_missing_field_defaults_zero():
    _, race, team = _priced_team("up2", cost=1000)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    # Pre-create with a non-zero count to prove the absent key zeroes it out.
    TeamExtra.objects.create(team=team, race_extra=transfer, count=5)

    # No "extra_transfer" key → count must be written back to 0.
    upsert_team_extras(team, {}, race)
    te = TeamExtra.objects.get(team=team, race_extra=transfer)
    assert te.count == 0


@pytest.mark.django_db
def test_upsert_team_extras_skips_inactive_extras():
    _, race, team = _priced_team("up3", cost=1000)
    inactive = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, is_active=False
    )

    # Submitting a value for an inactive extra must not create a TeamExtra row.
    upsert_team_extras(team, {"extra_transfer": 2}, race)
    assert not TeamExtra.objects.filter(team=team, race_extra=inactive).exists()


@pytest.mark.django_db
def test_create_team_payment_returns_none_when_cost_zero(rf):
    owner, race, team = _priced_team("cp1", cost=1000, ucount=2, paid_people=2)
    request = rf.post("/")
    request.user = owner

    # Fully paid people, no extras → cost 0 → no payment, no redirect.
    result = create_team_payment(request, team, race)

    assert result is None
    assert not Payment.objects.filter(team=team).exists()


# --- Race edit page: «Доп-услуги» (add-on) configuration (Task 5) ---


@pytest.mark.django_db
def test_race_edit_post_extras_reconcile_add_update_delete():
    user = User.objects.create_user(username="xa1", password="p", email="xa1@e.com")
    race = _make_race(slug="xa1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    keep = RaceExtra.objects.create(
        race=race, code="map", name="Карты", price=200, free_per_team=2, order=0
    )
    drop = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, order=1
    )

    extras = json.dumps(
        [
            {
                "id": keep.id,
                "code": "map",
                "name": "Доп. карты",
                "price": 250,
                "free_per_team": 2,
                "is_active": True,
            },
            {
                "id": None,
                "code": "breakfast",
                "name": "Завтрак",
                "price": 300,
                "free_per_team": 0,
                "is_active": True,
            },
        ]
    )
    data = _post_data(slug="xa1", extras_json=extras)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    # Unused row omitted from the payload is hard-deleted.
    assert not RaceExtra.objects.filter(id=drop.id).exists()
    keep.refresh_from_db()
    assert keep.name == "Доп. карты"
    assert keep.price == 250
    assert keep.order == 0
    breakfast = RaceExtra.objects.get(race=race, code="breakfast")
    assert breakfast.price == 300
    assert breakfast.order == 1


@pytest.mark.django_db
def test_race_edit_post_extra_in_use_deactivated_not_deleted():
    user = User.objects.create_user(username="xu1", password="p", email="xu1@e.com")
    race = _make_race(slug="xu1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    cat = _make_category(race)
    team = _make_team(user, cat)
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    TeamExtra.objects.create(team=team, race_extra=extra, count=1)

    # Keep the team's category in the payload (so the category reconcile does
    # not abort), but omit the extra entirely — it must deactivate, not delete.
    keep_cat = json.dumps([_cat_row(id=cat.id, code=cat.code, name=cat.name)])
    data = _post_data(slug="xu1", categories_json=keep_cat, extras_json="[]")
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    extra.refresh_from_db()
    # In-use row is softly deactivated, never deleted (PROTECT backstop).
    assert extra.is_active is False


@pytest.mark.django_db
def test_race_edit_post_extra_cross_race_id_treated_as_new():
    user = User.objects.create_user(username="xr1", password="p", email="xr1@e.com")
    race = _make_race(slug="xr1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    other = _make_race(slug="xr1b")
    other_extra = RaceExtra.objects.create(
        race=other, code="transfer", name="Трансфер", price=500
    )

    # Submit the other race's extra id — must create a new row, not hijack theirs.
    extras = json.dumps(
        [
            {
                "id": other_extra.id,
                "code": "transfer",
                "name": "Трансфер",
                "price": 100,
                "free_per_team": 0,
                "is_active": True,
            }
        ]
    )
    data = _post_data(slug="xr1", extras_json=extras)
    resp = _edit_post(f"/race/{race.slug}/edit/", user, data, race_slug=race.slug)

    assert resp.status_code == 302
    # The other race's extra is untouched.
    other_extra.refresh_from_db()
    assert other_extra.race_id == other.id and other_extra.price == 500
    # Our race got a brand-new extra instead of hijacking the other's.
    new_extra = RaceExtra.objects.get(race=race, code="transfer")
    assert new_extra.id != other_extra.id
    assert new_extra.price == 100


@pytest.mark.django_db
def test_race_edit_post_extra_duplicate_code_rejected():
    user = User.objects.create_user(username="xd1", password="p", email="xd1@e.com")
    race = _make_race(slug="xd1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    dup = json.dumps(
        [
            {
                "id": None,
                "code": "transfer",
                "name": "A",
                "price": 1,
                "is_active": True,
            },
            {
                "id": None,
                "code": "transfer",
                "name": "B",
                "price": 2,
                "is_active": True,
            },
        ]
    )
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="xd1", extras_json=dup),
        race_slug=race.slug,
    )
    assert resp.status_code == 200
    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not RaceExtra.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_post_extra_invalid_code_rejected():
    user = User.objects.create_user(username="xc1", password="p", email="xc1@e.com")
    race = _make_race(slug="xc1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    # Blank code.
    blank = json.dumps([{"id": None, "code": "", "name": "X", "price": 1}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="xc1", extras_json=blank),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    # Code with disallowed characters (uppercase / digits).
    bad = json.dumps([{"id": None, "code": "Map1", "name": "X", "price": 1}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="xc1", extras_json=bad),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    # Missing name.
    noname = json.dumps([{"id": None, "code": "transfer", "name": "", "price": 1}])
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="xc1", extras_json=noname),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not RaceExtra.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_post_extra_negative_values_rejected():
    user = User.objects.create_user(username="xn1", password="p", email="xn1@e.com")
    race = _make_race(slug="xn1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)

    neg_price = json.dumps(
        [{"id": None, "code": "transfer", "name": "X", "price": -5, "free_per_team": 0}]
    )
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="xn1", extras_json=neg_price),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    neg_free = json.dumps(
        [{"id": None, "code": "transfer", "name": "X", "price": 0, "free_per_team": -1}]
    )
    resp = _edit_post(
        f"/race/{race.slug}/edit/",
        user,
        _post_data(slug="xn1", extras_json=neg_free),
        race_slug=race.slug,
    )
    assert resp.status_code == 200

    race.refresh_from_db()
    assert race.name == "Teams Race"
    assert not RaceExtra.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_race_edit_get_serializes_existing_extras_with_usage_flag():
    user = User.objects.create_user(username="xg1", password="p", email="xg1@e.com")
    race = _make_race(slug="xg1")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    cat = _make_category(race)
    team = _make_team(user, cat)
    used = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, order=0
    )
    TeamExtra.objects.create(team=team, race_extra=used, count=1)
    RaceExtra.objects.create(race=race, code="breakfast", name="Завтрак", order=1)

    rows = RaceEditView._existing_extras(race)

    assert [r["code"] for r in rows] == ["transfer", "breakfast"]
    assert rows[0]["has_teams"] is True
    assert rows[1]["has_teams"] is False


# --- Legend (checkpoints) bulk editor -------------------------------------


def _legend_post(rows):
    return {"checkpoints_json": json.dumps(rows)}


@pytest.mark.django_db
def test_legend_edit_get_anonymous_redirects_to_login(client):
    race = _make_race()
    resp = client.get(reverse("edit_legend", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 302
    assert reverse("login") in resp.url


@pytest.mark.django_db
def test_legend_edit_get_regular_user_forbidden(client, django_user_model):
    race = _make_race()
    user = django_user_model.objects.create_user(username="u", password="x")
    client.force_login(user)
    resp = client.get(reverse("edit_legend", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_legend_edit_get_superuser_returns_200_with_existing(client):
    from website.models import Checkpoint

    race = _make_race()
    Checkpoint.objects.create(race=race, number=3, cost=40, description="Мост")
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.get(reverse("edit_legend", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200
    data = _script_json(resp.content.decode(), "checkpoints-data")
    assert data[0]["number"] == 3
    assert data[0]["description"] == "Мост"


@pytest.mark.django_db
def test_legend_edit_post_creates_and_updates(client):
    from website.models import Checkpoint

    race = _make_race()
    existing = Checkpoint.objects.create(
        race=race, number=1, cost=10, description="old"
    )
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": existing.id,
                    "number": 1,
                    "type": "kp",
                    "cost": 15,
                    "description": "новое",
                    "is_legend_locked": False,
                },
                {
                    "id": None,
                    "number": 2,
                    "type": "start",
                    "cost": 0,
                    "description": "Старт",
                    "is_legend_locked": False,
                },
            ]
        ),
    )
    assert resp.status_code == 302
    existing.refresh_from_db()
    assert existing.cost == 15 and existing.description == "новое"
    created = Checkpoint.objects.get(race=race, number=2)
    assert created.type == "start"


@pytest.mark.django_db
def test_legend_lock_toggle_manages_checkpoint_secret(client):
    """Toggling is_legend_locked via the editor must fire the crypto signals.

    A locked КП gets a CheckpointSecret (sealed legend); unlocking removes it.
    A bulk QuerySet.update() would skip this — the regression guard for the
    cleartext-leak risk.
    """
    from website.models import Checkpoint
    from website.models.checkpoint import CheckpointSecret

    race = _make_race()
    cp = Checkpoint.objects.create(race=race, number=1, cost=30, description="секрет")
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)
    url = reverse("edit_legend", kwargs={"race_slug": race.slug})

    client.post(
        url,
        _legend_post(
            [
                {
                    "id": cp.id,
                    "number": 1,
                    "type": "kp",
                    "cost": 30,
                    "description": "секрет",
                    "is_legend_locked": True,
                }
            ]
        ),
    )
    assert CheckpointSecret.objects.filter(checkpoint=cp).exists()

    client.post(
        url,
        _legend_post(
            [
                {
                    "id": cp.id,
                    "number": 1,
                    "type": "kp",
                    "cost": 30,
                    "description": "секрет",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert not CheckpointSecret.objects.filter(checkpoint=cp).exists()


@pytest.mark.django_db
def test_legend_delete_untagged_checkpoint(client):
    from website.models import Checkpoint

    race = _make_race()
    keep = Checkpoint.objects.create(race=race, number=1, cost=10, description="a")
    drop = Checkpoint.objects.create(race=race, number=2, cost=20, description="b")
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": keep.id,
                    "number": 1,
                    "type": "kp",
                    "cost": 10,
                    "description": "a",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 302
    assert Checkpoint.objects.filter(race=race).count() == 1
    assert not Checkpoint.objects.filter(id=drop.id).exists()


@pytest.mark.django_db
def test_legend_delete_tagged_checkpoint_refused(client):
    from website.models import Checkpoint
    from website.models.checkpoint import CheckpointTag

    race = _make_race()
    tagged = Checkpoint.objects.create(race=race, number=2, cost=20, description="b")
    CheckpointTag.objects.create(point=tagged, nfc_uid="aa:bb:cc")
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    # Submit an empty legend → would delete the tagged КП, which must be refused.
    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post([]),
    )
    assert resp.status_code == 200
    assert Checkpoint.objects.filter(id=tagged.id).exists()
    assert any("NFC" in e for e in resp.context["form_errors"])


@pytest.mark.django_db
def test_legend_post_invalid_type_reports_row_error(client):
    from website.models import Checkpoint

    race = _make_race()
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": None,
                    "number": 1,
                    "type": "bogus",
                    "cost": 10,
                    "description": "x",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 200
    assert Checkpoint.objects.filter(race=race).count() == 0
    errors = _script_json(resp.content.decode(), "checkpoint-errors")
    assert "type" in errors["0"]


@pytest.mark.django_db
def test_legend_post_saves_valid_color(client):
    from website.models import Checkpoint

    race = _make_race()
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": None,
                    "number": 1,
                    "type": "kp",
                    "color": "red",
                    "cost": 10,
                    "description": "x",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 302
    cp = Checkpoint.objects.get(race=race, number=1)
    assert cp.color == "red"


@pytest.mark.django_db
def test_legend_post_unknown_color_reports_row_error(client):
    from website.models import Checkpoint

    race = _make_race()
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": None,
                    "number": 1,
                    "type": "kp",
                    "color": "rainbow",
                    "cost": 10,
                    "description": "x",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 200
    assert Checkpoint.objects.filter(race=race).count() == 0
    errors = _script_json(resp.content.decode(), "checkpoint-errors")
    assert "color" in errors["0"]


@pytest.mark.django_db
def test_legend_post_missing_color_defaults_empty(client):
    from website.models import Checkpoint

    race = _make_race()
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": None,
                    "number": 1,
                    "type": "kp",
                    "cost": 10,
                    "description": "x",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 302
    cp = Checkpoint.objects.get(race=race, number=1)
    assert cp.color == ""


@pytest.mark.django_db
def test_legend_color_round_trip_on_existing(client):
    from website.models import Checkpoint

    race = _make_race()
    existing = Checkpoint.objects.create(
        race=race, number=1, cost=10, description="a", color="blue"
    )
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    # The existing КП's color is surfaced in the rendered checkpoints-data island.
    resp = client.get(reverse("edit_legend", kwargs={"race_slug": race.slug}))
    rows = _script_json(resp.content.decode(), "checkpoints-data")
    assert rows[0]["color"] == "blue"

    # Editing to a different color persists.
    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": existing.id,
                    "number": 1,
                    "type": "kp",
                    "color": "green",
                    "cost": 10,
                    "description": "a",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 302
    existing.refresh_from_db()
    assert existing.color == "green"


@pytest.mark.django_db
def test_legend_color_cleared_to_empty_on_existing(client):
    from website.models import Checkpoint

    race = _make_race()
    existing = Checkpoint.objects.create(
        race=race, number=1, cost=10, description="a", color="blue"
    )
    superuser = User.objects.create_superuser("admin2", "admin2@b.c", "pw")
    client.force_login(superuser)

    resp = client.post(
        reverse("edit_legend", kwargs={"race_slug": race.slug}),
        _legend_post(
            [
                {
                    "id": existing.id,
                    "number": 1,
                    "type": "kp",
                    "color": "",
                    "cost": 10,
                    "description": "a",
                    "is_legend_locked": False,
                }
            ]
        ),
    )
    assert resp.status_code == 302
    existing.refresh_from_db()
    assert existing.color == ""


@pytest.mark.django_db
def test_legend_config_island_includes_colors(client):
    race = _make_race()
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.get(reverse("edit_legend", kwargs={"race_slug": race.slug}))
    config = _script_json(resp.content.decode(), "legend-config")
    values = {c["value"] for c in config["colors"]}
    assert {"", "red", "blue", "green", "yellow", "orange", "purple"} <= values


# ---------------------------------------------------------------------------
# Legend codes page (read-only NFC codes)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_legend_codes_get_anonymous_redirects_to_login(client):
    race = _make_race()
    url = reverse("legend_codes", kwargs={"race_slug": race.slug})
    resp = client.get(url)
    assert resp.status_code == 302
    assert reverse("login") in resp.url
    assert "?next=" in resp.url
    assert f"/race/{race.slug}/legend/codes/" in resp.url


@pytest.mark.django_db
def test_legend_codes_get_regular_user_forbidden(client, django_user_model):
    race = _make_race()
    user = django_user_model.objects.create_user(username="u", password="x")
    client.force_login(user)
    resp = client.get(reverse("legend_codes", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_legend_codes_lists_tags_with_hex_and_placeholder(client):
    from website.models import Checkpoint
    from website.models.checkpoint import CheckpointTag

    race = _make_race()
    cp2 = Checkpoint.objects.create(race=race, number=2, cost=20, description="b")
    cp1 = Checkpoint.objects.create(race=race, number=1, cost=10, description="a")
    with_code = CheckpointTag.objects.create(
        point=cp1, nfc_uid="aa:bb:cc", code=b"\x01\x02\x03"
    )
    without_code = CheckpointTag.objects.create(point=cp2, nfc_uid="dd:ee:ff")
    # The post_save signal auto-mints a code; clear it via update() (bypasses
    # signals) to exercise the "—" placeholder the command also shows.
    CheckpointTag.objects.filter(id=without_code.id).update(code=None)
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    client.force_login(superuser)

    resp = client.get(reverse("legend_codes", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200
    rows = resp.context["rows"]
    # Ordered by point number, so КП 1 (with code) comes first.
    assert rows[0]["nfc_uid"] == with_code.nfc_uid
    assert rows[0]["number"] == 1
    assert rows[0]["code"] == "010203"
    assert rows[1]["nfc_uid"] == without_code.nfc_uid
    assert rows[1]["code"] == "—"


@pytest.mark.django_db
def test_legend_codes_get_race_admin_allowed(client, django_user_model):
    race = _make_race()
    user = django_user_model.objects.create_user(username="admin_user", password="x")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    client.force_login(user)
    resp = client.get(reverse("legend_codes", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_legend_codes_get_race_moderator_forbidden(client, django_user_model):
    race = _make_race()
    user = django_user_model.objects.create_user(username="mod_user", password="x")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.MODERATOR)
    client.force_login(user)
    resp = client.get(reverse("legend_codes", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
