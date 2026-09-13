import datetime
import itertools
import json
import re

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import resolve, reverse

from apps.mobile.models import JudgeScan, Mark, MarkPhoto, MarkPresent, TrackPoint
from apps.race.app_data import build_overview, build_team_timeline, format_ms
from apps.race.forms import RaceForm
from apps.race.models import Protocol
from apps.race.permissions import can_edit_race
from apps.race.results import build_protocol, freeze_protocol
from apps.race.views import (
    ProtocolBuildView,
    ProtocolFreezeView,
    ProtocolView,
    RaceAppDataTeamView,
    RaceAppDataView,
    RaceEditView,
    RaceMapMarksView,
    RaceMapPositionsView,
    RaceMapTrackView,
    RaceMapView,
    RacePageView,
    RaceTeamsView,
)
from website.models import Race
from website.models.checkpoint import Checkpoint
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


def _attach_messages(request):
    """Attach a message storage to a bare ``RequestFactory`` request.

    The build/freeze views call ``messages.success``/``messages.info``, which
    need ``request._messages`` — normally set by ``MessageMiddleware``, which
    ``RequestFactory`` requests never go through. Standard Django test recipe.
    """
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))
    return request


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
    assert RaceTeamsView.build_context(race, superuser)["can_edit_race"] is False
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    assert teams["Mine"]["action"] == "Посмотреть"
    assert teams["Theirs"]["mine"] is False
    assert "edit" not in teams["Theirs"]
    assert context["has_team_actions"] is True


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
def test_all_teams_renders_add_action_for_assigned_superuser(client, django_user_model):
    race = _make_race(slug="ru2a")
    superuser = django_user_model.objects.create_superuser(
        username="su2", password="p", email="su2@example.com"
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)

    resp = client.get(reverse("all_teams", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "teams-add" in html
    # «Редактировать гонку» lives on the race page now, not on teams.
    assert reverse("edit_race", args=[race.slug]) not in html
    # The teams toolbar keeps «+ Команда».
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
@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize(
    "role",
    ["anonymous", "visitor", "staff", "other_admin", "admin", "moderator", "superuser"],
)
def test_race_overview_visibility(client, published, role):
    race = Race.objects.create(
        name="Неанонсированный старт", slug="draft-overview", is_published=published
    )
    from website.models import NewsPost

    post = NewsPost.objects.create(
        title="Секретная новость", content="Описание", race=race
    )
    if role != "anonymous":
        user = User.objects.create_user(
            username=role, is_staff=role == "staff", is_superuser=role == "superuser"
        )
        if role in {"admin", "moderator", "other_admin"}:
            assigned_race = (
                _make_race(slug="other-draft-race") if role == "other_admin" else race
            )
            RaceAdmin.objects.create(
                race=assigned_race,
                user=user,
                role=(
                    RaceAdmin.Role.MODERATOR
                    if role == "moderator"
                    else RaceAdmin.Role.ADMIN
                ),
            )
        client.force_login(user)

    response = client.get(reverse("race", args=[race.slug]))
    allowed = published or role in {"admin", "moderator", "superuser"}
    assert response.status_code == (200 if allowed else 404)
    if allowed:
        assert response.context["race"] == race
        # The feed uses public visibility rules even in a private race preview.
        assert (post in response.context["news_list"]) is published
        if not published:
            assert client.get(post.get_absolute_url()).status_code == 200
    else:
        html = response.content.decode()
        assert race.name not in html
        assert post.title not in html


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
    # Compact shared race header + persistent section tabs.
    assert 'class="race-hero"' in html
    assert 'class="section-tabs"' in html
    assert 'class="section-tab is-active"' in html
    assert reverse("race", args=[race.slug]) in html
    assert reverse("all_teams", args=[race.slug]) in html
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
    # The table uses the full page width; duplicate sidebar breakdown is gone.
    assert 'id="brk"' not in html
    assert 'id="resetCat"' not in html
    # both JSON blocks present and parseable
    _script_json(html, "teams-data")
    _script_json(html, "categories-data")
    # teams.js wired up
    assert "js/teams.js" in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "active_url_name"),
    [("race", "race"), ("all_teams", "all_teams")],
)
def test_race_tabs_keep_labels_active_state_and_team_count(
    client, url_name, active_url_name
):
    owner = User.objects.create_user(
        username=f"tabs-{url_name}",
        password="p",
        email=f"tabs-{url_name}@example.com",
    )
    race = _make_race(slug=f"tabs-{url_name}")
    category = _make_category(race)
    _make_team(owner, category, teamname="Оплаченная", paid_people=2)
    _make_team(owner, category, teamname="Неоплаченная", paid_people=0)

    response = client.get(reverse(url_name, args=[race.slug]))
    html = response.content.decode()
    nav_match = re.search(
        r'<nav\b[^>]*aria-label="Разделы гонки"[^>]*>.*?</nav>',
        html,
        re.DOTALL,
    )

    assert response.status_code == 200
    assert nav_match, "Race section tabs were not rendered"
    nav = nav_match.group(0)
    current_links = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', nav)
    compact_nav = " ".join(nav.split())

    assert "Обзор" in nav
    assert "Команды <span>1</span>" in compact_nav
    assert len(current_links) == 1
    assert 'class="section-tab is-active"' in current_links[0]
    expected_href = reverse(active_url_name, args=[race.slug])
    assert f'href="{expected_href}"' in current_links[0]


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
def test_race_page_shows_owned_team_with_explicit_edit_action(client):
    owner = User.objects.create_user(
        username="team-owner", password="p", email="owner@example.com"
    )
    race = _make_race(slug="owned-team")
    race.is_teams_editable = True
    race.save(update_fields=["is_teams_editable"])
    category = _make_category(race)
    team = _make_team(
        owner,
        category,
        teamname="Лесные коты",
        start_number="18",
        city="Уфа",
    )
    client.force_login(owner)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert len(resp.context["owned_teams"]) == 1
    html = resp.content.decode()
    assert "Ваша команда" in html
    assert "Лесные коты" in html
    assert "№ 18" in html
    assert "Редактировать команду" in html
    assert reverse("edit_team", args=[team.id]) in html


@pytest.mark.django_db
def test_race_page_lists_multiple_owned_teams(client):
    owner = User.objects.create_user(
        username="multi-owner", password="p", email="multi@example.com"
    )
    race = _make_race(slug="owned-teams")
    category = _make_category(race)
    first = _make_team(owner, category, teamname="Первая", start_number="1")
    second = _make_team(owner, category, teamname="Вторая", start_number="2")
    client.force_login(owner)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Ваши команды" in html
    assert reverse("edit_team", args=[first.id]) in html
    assert reverse("edit_team", args=[second.id]) in html


@pytest.mark.django_db
def test_race_page_keeps_owned_team_link_when_editing_is_closed(client):
    owner = User.objects.create_user(
        username="locked-owner", password="p", email="locked@example.com"
    )
    race = _make_race(slug="locked-team")
    category = _make_category(race)
    team = _make_team(owner, category, teamname="После финиша")
    client.force_login(owner)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Редактирование закрыто" in html
    assert "Посмотреть команду" in html
    assert reverse("edit_team", args=[team.id]) in html


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
def test_race_page_assigned_superuser_sees_edit_and_new_buttons(client):
    admin = User.objects.create_superuser(
        username="su-buttons", password="p", email="su-buttons@example.com"
    )
    race = _make_race(slug="su-btn")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)

    resp = client.get(reverse("race", args=[race.slug]))

    assert resp.status_code == 200
    assert resp.context["can_edit_race"] is True
    html = resp.content.decode()
    assert "card-admin" in html  # «Управление» panel in the sidebar
    assert reverse("edit_race", args=[race.slug]) in html
    assert reverse("add_race") in html
    assert "+ Новая гонка" in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role",
    ["anonymous", "visitor", "staff", "other_admin", "admin", "moderator", "superuser"],
)
def test_race_administrators_visibility(client, role):
    race = _make_race(slug="administrators-visibility")
    assigned = User.objects.create_user(username="private-roster-member")
    RaceAdmin.objects.create(race=race, user=assigned)
    if role != "anonymous":
        viewer = User.objects.create_user(
            username=role, is_staff=role == "staff", is_superuser=role == "superuser"
        )
        if role in {"admin", "moderator", "other_admin"}:
            RaceAdmin.objects.create(
                race=_make_race(slug="other-roster") if role == "other_admin" else race,
                user=viewer,
                role=(
                    RaceAdmin.Role.MODERATOR
                    if role == "moderator"
                    else RaceAdmin.Role.ADMIN
                ),
            )
        client.force_login(viewer)

    response = client.get(reverse("race", args=[race.slug]))

    assert response.status_code == 200
    allowed = role == "admin"
    html = response.content.decode()
    assert ('id="race-administrators-title"' in html) is allowed
    assert ("private-roster-member" in html) is allowed
    assert ("race_administrators" in response.context) is allowed


@pytest.mark.django_db
def test_race_administrators_names_roles_and_order(client):
    race = _make_race(slug="administrators-order")
    for username, first_name, last_name, role in [
        ("z-admin", "Zoe", "Brown", RaceAdmin.Role.ADMIN),
        ("b-moderator", "Bella", "Smith", RaceAdmin.Role.MODERATOR),
        ("a-admin", "alice", "Jones", RaceAdmin.Role.ADMIN),
        ("aaron", "", "", RaceAdmin.Role.MODERATOR),
    ]:
        member = User.objects.create_user(
            username=username, first_name=first_name, last_name=last_name
        )
        RaceAdmin.objects.create(race=race, user=member, role=role)
    client.force_login(User.objects.get(username="a-admin"))
    outsider = User.objects.create_user(username="other-race-member")
    RaceAdmin.objects.create(race=_make_race(slug="other-members"), user=outsider)

    response = client.get(reverse("race", args=[race.slug]))

    assert response.status_code == 200
    html = response.content.decode()
    card = html.split('aria-labelledby="race-administrators-title"', 1)[1].split(
        "</section>", 1
    )[0]
    names = ["alice Jones", "Zoe Brown", "aaron", "Bella Smith"]
    for assignment in race.race_admins.select_related("user"):
        name = assignment.user.get_full_name() or assignment.user.get_username()
        assert (
            f'{name} <small class="race-administrators-id">'
            f"ID: {assignment.user_id}</small>"
        ) in card
    assert [
        member["name"] for member in response.context["race_administrators"]
    ] == names
    assert [card.index(name) for name in names] == sorted(
        card.index(name) for name in names
    )
    assert (
        card.count('<span class="race-administrators-role">Администратор</span>') == 2
    )
    assert card.count('<span class="race-administrators-role">Модератор</span>') == 2
    assert "other-race-member" not in card
    assert "site-superuser" not in card


@pytest.mark.django_db
def test_race_administrators_template_empty_state():
    race = _make_race(slug="administrators-empty")
    # Defensive template fallback: authorized viewers normally have an assignment.
    context = RacePageView.build_context(race)
    context.update(can_edit_race=True, race_administrators=[], user=AnonymousUser())
    html = render_to_string("race/race_page.html", context)
    assert "Администраторы и модераторы не назначены" in html


# --- can_edit_race access-control matrix ---


@pytest.mark.django_db
def test_can_edit_race_superuser_requires_assignment_for_each_race():
    admin = User.objects.create_superuser(
        username="su", password="p", email="su@example.com"
    )
    race = _make_race(slug="ce1")
    other = _make_race(slug="ce1b")

    assert can_edit_race(admin, race) is False
    assert can_edit_race(admin, other) is False
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    RaceAdmin.objects.create(race=other, user=admin, role=RaceAdmin.Role.MODERATOR)
    assert can_edit_race(admin, race) is True
    assert can_edit_race(admin, other) is False


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

    total, lines, _ = compute_team_charge(team, race)

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

    total, lines, _ = compute_team_charge(team, race)

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

    total, lines, _ = compute_team_charge(team, race)

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

    total, lines, _ = compute_team_charge(team, race)

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

    total, lines, _ = compute_team_charge(team, race)

    assert total == 1000  # 2 × 500
    assert lines == [ExtraCharge(race_extra=transfer, count=2, unit_price=500)]


@pytest.mark.django_db
def test_compute_team_charge_floors_at_zero():
    # Over-paid people (refund-like) must not produce a negative total.
    _, race, team = _priced_team("ch6", cost=1000, ucount=1, paid_people=3)

    total, lines, _ = compute_team_charge(team, race)

    assert total == 0
    assert lines == []


@pytest.mark.django_db
def test_compute_team_charge_ignores_inactive_extra():
    _, race, team = _priced_team("ch7", cost=1000, ucount=2, paid_people=2)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, is_active=False
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=2, count_paid=0)

    total, lines, _ = compute_team_charge(team, race)

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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    CheckpointTag.objects.create(checkpoint=tagged, nfc_uid="aa:bb:cc")
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
        checkpoint=cp1, nfc_uid="aa:bb:cc", code=b"\x01\x02\x03"
    )
    without_code = CheckpointTag.objects.create(checkpoint=cp2, nfc_uid="dd:ee:ff")
    # The post_save signal auto-mints a code; clear it via update() (bypasses
    # signals) to exercise the "—" placeholder the command also shows.
    CheckpointTag.objects.filter(id=without_code.id).update(code=None)
    superuser = User.objects.create_superuser("admin", "a@b.c", "pw")
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
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
# Protocol / ProtocolRow models (Task 1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_protocol_created_with_default_status_draft():
    from apps.race.models import Protocol

    race = _make_race()
    protocol = Protocol.objects.create(race=race)
    assert protocol.status == Protocol.DRAFT
    assert protocol.frozen_at is None
    assert protocol.created_by is None


@pytest.mark.django_db
def test_protocol_related_name_on_race():
    from apps.race.models import Protocol

    race = _make_race()
    protocol = Protocol.objects.create(race=race)
    assert list(race.protocols.all()) == [protocol]


@pytest.mark.django_db
def test_protocol_row_related_name_on_protocol():
    from apps.race.models import Protocol, ProtocolRow

    race = _make_race()
    protocol = Protocol.objects.create(race=race)
    row = ProtocolRow.objects.create(protocol=protocol, team_id=1, category_id=1)
    assert list(protocol.rows.all()) == [row]


@pytest.mark.django_db
def test_protocol_row_cascade_deleted_with_protocol():
    from apps.race.models import Protocol, ProtocolRow

    race = _make_race()
    protocol = Protocol.objects.create(race=race)
    row = ProtocolRow.objects.create(protocol=protocol, team_id=1, category_id=1)
    protocol.delete()
    assert not ProtocolRow.objects.filter(id=row.id).exists()


@pytest.mark.django_db
def test_protocol_frozen_at_and_created_by_allow_null(django_user_model):
    from apps.race.models import Protocol

    race = _make_race()
    user = django_user_model.objects.create_user(username="creator", password="x")

    protocol = Protocol.objects.create(race=race, created_by=user)
    assert protocol.frozen_at is None

    protocol.created_by = None
    protocol.save()
    protocol.refresh_from_db()
    assert protocol.created_by is None


# ---------------------------------------------------------------------------
# build_protocol / freeze_protocol service (Task 2)
# ---------------------------------------------------------------------------


def _make_checkpoint(race, number, cost):
    return Checkpoint.objects.create(race=race, number=number, cost=cost)


_mark_id_seq = itertools.count(1)


def _make_mark(
    team,
    checkpoint,
    method="nfc",
    verified=True,
    present_chips=("chip1", "chip2"),
    expected=None,
    wall_ms=1,
    trusted_ms=None,
):
    """Create a mobile ``Mark`` (+ ``MarkPresent`` roster) for the protocol tests.

    ``checkpoint`` may be a ``Checkpoint`` (its ``id`` is used as
    ``Mark.checkpoint_id``) or a raw int (an orphan/unknown КП). ``present_chips``
    is the tuple of participant ``nfc_uid``s scanned at the take; its distinct
    count is compared against ``team.ucount`` (the roster) to decide completeness.
    The default roster covers the default ``ucount=2`` team. ``expected_count`` is
    stored but no longer used for scoring (server recomputes against the roster).
    """
    cp_id = getattr(checkpoint, "id", checkpoint)
    mark = Mark.objects.create(
        id=f"mark-{next(_mark_id_seq)}",
        team=team,
        race=team.category2.race,
        source_install_id="test",
        checkpoint_id=cp_id,
        method=method,
        cp_code="",
        cp_nfc_uid="",
        expected_count=len(present_chips) if expected is None else expected,
        complete=True,
        verified=verified,
        wall_ms=wall_ms,
        trusted_ms=trusted_ms,
    )
    for i, uid in enumerate(present_chips, start=1):
        MarkPresent.objects.create(
            mark=mark, nfc_uid=uid, code="", number=i, number_in_team=i
        )
    return mark


def _make_started_team(owner, category, start_time=1000, finish_time=0, **kwargs):
    return _make_team(
        owner,
        category,
        start_time=start_time,
        finish_time=finish_time,
        **kwargs,
    )


@pytest.mark.django_db
def test_build_protocol_creates_rows_with_scores_and_places(django_user_model):
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)
    cp2 = _make_checkpoint(race, 2, 20)

    owner_a = django_user_model.objects.create_user(username="owner_a", password="x")
    owner_b = django_user_model.objects.create_user(username="owner_b", password="x")

    team_a = _make_started_team(
        owner_a, category, teamname="A", finish_time=1000 + 3_600_000
    )
    team_b = _make_started_team(
        owner_b, category, teamname="B", finish_time=1000 + 1_800_000
    )

    _make_mark(team_a, cp1, present_chips=("a1", "a2"), wall_ms=1)
    _make_mark(team_a, cp2, present_chips=("a1", "a2"), wall_ms=2)
    _make_mark(team_b, cp1, present_chips=("b1", "b2"), wall_ms=1)

    protocol = build_protocol(race, None)

    rows = {row.team_id: row for row in protocol.rows.all()}
    assert rows[team_a.id].total_score == 30
    assert rows[team_a.id].nfc_score == 30
    assert rows[team_a.id].chips_count == 2
    assert rows[team_a.id].duration_ms == 3_600_000
    assert rows[team_b.id].total_score == 10
    assert rows[team_b.id].duration_ms == 1_800_000

    # A scores higher despite taking longer -> places 1st.
    assert rows[team_a.id].place == 1
    assert rows[team_b.id].place == 2


@pytest.mark.django_db
def test_build_protocol_penalty_from_category(django_user_model):
    race = _make_race()
    category = _make_category(race)
    category.control_time = 60
    category.overtime_penalty = 2
    category.save()
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=1000 + 90 * 60 * 1000)
    _make_mark(team, cp1)

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.total_score == 10
    assert row.penalty == 60  # 30 min overtime * 2 pts/min
    assert row.final_score == row.total_score - 60


@pytest.mark.django_db
def test_build_protocol_no_penalty_when_control_time_zero(django_user_model):
    race = _make_race()
    category = _make_category(race)  # control_time defaults to 0
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=1000 + 90 * 60 * 1000)
    _make_mark(team, cp1)

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.penalty == 0
    assert row.final_score == row.total_score


@pytest.mark.django_db
def test_build_protocol_penalty_magnitude_matches_old_one_point_per_minute(
    django_user_model,
):
    race = _make_race()
    category = _make_category(race)
    category.control_time = 60
    category.overtime_penalty = 1
    category.save()

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=1000 + 75 * 60 * 1000)

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.penalty == 15  # matches old view's 1 point per overtime minute


@pytest.mark.django_db
def test_build_protocol_orphan_checkpoint_number_does_not_crash(django_user_model):
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)
    # checkpoint_id 999999 has no Checkpoint row at all -> orphan
    _make_mark(team, 999999, wall_ms=1)
    _make_mark(team, cp1, wall_ms=2)

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    # only the known point (cost 10) contributes to score.
    assert row.total_score == 10
    assert row.nfc_count == 1


@pytest.mark.django_db
def test_build_protocol_incomplete_nfc_mark_not_counted(django_user_model):
    """An NFC take with fewer present chips than the roster (``team.ucount``,
    not all participants scanned) does not count as a taken КП."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    # ucount 2, only 1 chip scanned -> incomplete against the roster.
    team = _make_started_team(owner, category, finish_time=2000, ucount=2)
    _make_mark(team, cp1, present_chips=("chip1",))

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.nfc_count == 0
    assert row.total_score == 0
    # the lone scanned bracelet still shows up in the chip count.
    assert row.chips_count == 1


@pytest.mark.django_db
def test_build_protocol_complete_nfc_mark_counts(django_user_model):
    """All roster participants scanned (present >= ucount) -> the КП counts."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000, ucount=2)
    _make_mark(team, cp1, present_chips=("chip1", "chip2"))

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.nfc_count == 1
    assert row.total_score == 10
    assert row.chips_count == 2


@pytest.mark.django_db
def test_build_protocol_unverified_nfc_mark_not_counted(django_user_model):
    """A ``verified=False`` NFC mark (no physical КП scan proof) is ignored."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)
    _make_mark(team, cp1, verified=False)

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.nfc_count == 0
    assert row.total_score == 0


@pytest.mark.django_db
def test_build_protocol_photo_mark_counts_without_participant_check(django_user_model):
    """A photo take counts on a known КП with no verified/participant check."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)
    # verified False, no present roster, but photo counts anyway.
    _make_mark(team, cp1, method="photo", verified=False, present_chips=())

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.photo_count == 1
    assert row.photo_score == 10
    assert row.total_score == 10


@pytest.mark.django_db
def test_build_protocol_nfc_wins_over_photo_same_checkpoint(django_user_model):
    """When the same КП has both an NFC and a photo take, NFC wins (scored once)."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)
    _make_mark(team, cp1, method="nfc")
    _make_mark(team, cp1, method="photo", verified=False, present_chips=())

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.nfc_count == 1
    assert row.photo_count == 0
    assert row.total_score == 10


@pytest.mark.django_db
def test_build_protocol_chips_count_distinct_across_marks(django_user_model):
    """chips_count is distinct bracelets across all the team's marks; the
    ``nfc_uid=null`` sentinel row does not count."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)
    cp2 = _make_checkpoint(race, 2, 20)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)
    _make_mark(team, cp1, present_chips=("a", "b"), wall_ms=1)
    _make_mark(team, cp2, present_chips=("b", "c"), wall_ms=2)
    # a "no snapshot" sentinel row must not inflate the count.
    sentinel = _make_mark(team, cp1, present_chips=(), wall_ms=3)
    MarkPresent.objects.create(
        mark=sentinel, nfc_uid=None, code=None, number=0, number_in_team=0
    )

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.chips_count == 3  # a, b, c


@pytest.mark.django_db
def test_build_protocol_rebuild_reuses_draft_and_recomputes_rows(django_user_model):
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)

    protocol_1 = build_protocol(race, None)
    assert protocol_1.rows.count() == 1
    assert protocol_1.rows.get().total_score == 0

    _make_mark(team, cp1)
    protocol_2 = build_protocol(race, None)

    assert protocol_2.id == protocol_1.id
    assert protocol_2.rows.count() == 1
    assert protocol_2.rows.get().total_score == 10


@pytest.mark.django_db
def test_build_protocol_after_freeze_creates_new_draft_final_unchanged(
    django_user_model,
):
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)

    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, finish_time=2000)
    _make_mark(team, cp1, wall_ms=1)

    draft = build_protocol(race, None)
    final = freeze_protocol(race)
    assert final.id == draft.id
    assert final.status == Protocol.FINAL
    final_score_before = final.rows.get().total_score

    # New data appears after freeze.
    _make_mark(team, cp1, wall_ms=2)
    cp2 = _make_checkpoint(race, 2, 50)
    _make_mark(team, cp2, wall_ms=3)

    new_draft = build_protocol(race, None)
    assert new_draft.id != final.id
    assert new_draft.status == Protocol.DRAFT
    assert new_draft.rows.get().total_score == 60

    final.refresh_from_db()
    assert final.rows.get().total_score == final_score_before


@pytest.mark.django_db
def test_build_protocol_no_started_teams_returns_zero_rows():
    race = _make_race()
    _make_category(race)

    protocol = build_protocol(race, None)

    assert protocol.rows.count() == 0


@pytest.mark.django_db
def test_build_protocol_excludes_unpaid_teams(django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category, finish_time=2000, paid_people=0)

    protocol = build_protocol(race, None)

    assert protocol.rows.count() == 0


@pytest.mark.django_db
def test_build_protocol_excludes_unstarted_teams(django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_team(owner, category, start_time=0, finish_time=0)

    protocol = build_protocol(race, None)

    assert protocol.rows.count() == 0


@pytest.mark.django_db
def test_build_protocol_place_resets_per_category(django_user_model):
    race = _make_race()
    cat_a = _make_category(race, code="a", short_name="a", name="A")
    cat_b = _make_category(race, code="b", short_name="b", name="B")
    _make_checkpoint(race, 1, 10)
    cp2 = _make_checkpoint(race, 2, 20)

    owner_1 = django_user_model.objects.create_user(username="o1", password="x")
    owner_2 = django_user_model.objects.create_user(username="o2", password="x")
    owner_3 = django_user_model.objects.create_user(username="o3", password="x")
    owner_4 = django_user_model.objects.create_user(username="o4", password="x")

    a_hi = _make_started_team(owner_1, cat_a, teamname="A-hi", finish_time=2000)
    a_lo = _make_started_team(owner_2, cat_a, teamname="A-lo", finish_time=2000)
    b_hi = _make_started_team(owner_3, cat_b, teamname="B-hi", finish_time=2000)
    b_lo = _make_started_team(owner_4, cat_b, teamname="B-lo", finish_time=2000)

    _make_mark(a_hi, cp2)
    _make_mark(b_hi, cp2)

    protocol = build_protocol(race, None)
    rows = {row.team_id: row for row in protocol.rows.all()}

    # Each category has its own 1st/2nd place, independent of the other.
    assert rows[a_hi.id].place == 1
    assert rows[a_lo.id].place == 2
    assert rows[b_hi.id].place == 1
    assert rows[b_lo.id].place == 2


@pytest.mark.django_db
def test_build_protocol_ties_broken_deterministically_by_team_id(django_user_model):
    race = _make_race()
    category = _make_category(race)

    owner_1 = django_user_model.objects.create_user(username="t1", password="x")
    owner_2 = django_user_model.objects.create_user(username="t2", password="x")
    # Both teams tie on final_score (0) and duration_ms -> team_id tiebreaker.
    team_lo = _make_started_team(owner_1, category, teamname="Lo", finish_time=2000)
    team_hi = _make_started_team(owner_2, category, teamname="Hi", finish_time=2000)
    assert team_lo.id < team_hi.id

    protocol_1 = build_protocol(race, None)
    rows_1 = {row.team_id: row.place for row in protocol_1.rows.all()}

    protocol_2 = build_protocol(race, None)
    rows_2 = {row.team_id: row.place for row in protocol_2.rows.all()}

    assert rows_1 == rows_2
    assert rows_1[team_lo.id] == 1
    assert rows_1[team_hi.id] == 2


@pytest.mark.django_db
def test_build_protocol_start_number_accepts_full_team_field_length(
    django_user_model,
):
    """Regression: ``ProtocolRow.start_number`` must accept anything
    ``Team.start_number`` (max_length=50) can hold, or ``bulk_create`` raises
    a Postgres ``DataError`` and the whole build rolls back."""
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    long_start_number = "x" * 50
    _make_started_team(
        owner, category, finish_time=2000, start_number=long_start_number
    )

    protocol = build_protocol(race, None)

    assert protocol.rows.get().start_number == long_start_number


@pytest.mark.django_db
def test_team_members_lists_all_athletes_up_to_ucount(django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(
        owner,
        category,
        finish_time=2000,
        ucount=3,
        athlet1="Ivanov",
        athlet2="Petrov",
        athlet3="Sidorov",
    )

    protocol = build_protocol(race, None)
    row = protocol.rows.get(team_id=team.id)

    assert row.members == "Ivanov, Petrov, Sidorov"


@pytest.mark.django_db
def test_freeze_protocol_without_any_protocol_returns_none():
    race = _make_race()

    assert freeze_protocol(race) is None


@pytest.mark.django_db
def test_freeze_protocol_without_draft_returns_none(django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category, finish_time=2000)

    build_protocol(race, None)
    frozen = freeze_protocol(race)
    assert frozen.status == Protocol.FINAL

    # freezing again with no draft present is a no-op.
    assert freeze_protocol(race) is None


@pytest.mark.django_db(transaction=True)
def test_build_protocol_concurrent_first_build_creates_only_one_draft():
    """Two racing first builds on a race with no protocol yet must not both
    create a draft -- the ``select_for_update`` lock on the parent ``Race``
    row (Task 2) has to serialize the critical section even though the
    ``Protocol`` queryset both threads see is empty. ``Protocol.objects.create``
    is slowed down so the first thread is still holding the ``Race`` row lock
    when the second thread reaches its own ``select_for_update`` -- without
    this delay the two threads might simply run one after another and the
    test would pass even with a broken (unlocked) implementation.
    ``transaction=True`` gives each thread its own real DB transaction, which
    real row locking requires (a savepoint in the default wrapped-test
    transaction would not block a second thread)."""
    import threading
    import time
    from unittest.mock import patch

    from apps.race.models import Protocol as ProtocolModel

    race = _make_race()
    _make_category(race)

    real_create = ProtocolModel.objects.create

    def _slow_create(*args, **kwargs):
        obj = real_create(*args, **kwargs)
        time.sleep(0.3)
        return obj

    errors = []

    def _build():
        try:
            build_protocol(race, None)
        except Exception as exc:  # pragma: no cover - surfaced via errors list
            errors.append(exc)
        finally:
            from django.db import connection

            connection.close()

    with patch.object(ProtocolModel.objects, "create", side_effect=_slow_create):
        t1 = threading.Thread(target=_build)
        t2 = threading.Thread(target=_build)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert not errors
    assert Protocol.objects.filter(race=race).count() == 1


# ---------------------------------------------------------------------------
# ProtocolView (Task 3)
# ---------------------------------------------------------------------------
# The URL for this view is wired in Task 5, so these tests call it directly
# via RequestFactory instead of ``client``/``reverse()``.


@pytest.mark.django_db
def test_protocol_view_public_sees_only_final(rf, django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category, teamname="A", finish_time=2000)

    build_protocol(race, None)  # draft only, not frozen

    request = rf.get("/")
    request.user = AnonymousUser()
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category.id
    )
    assert response.status_code == 200
    assert "ещё не опубликован" in response.content.decode()

    freeze_protocol(race)
    request = rf.get("/")
    request.user = AnonymousUser()
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category.id
    )
    assert response.status_code == 200
    assert "A" in response.content.decode()


@pytest.mark.django_db
def test_protocol_view_admin_sees_draft(rf, django_user_model):
    race = _make_race()
    category = _make_category(race)
    admin = django_user_model.objects.create_user(username="radmin", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category, teamname="DraftTeam", finish_time=2000)

    build_protocol(race, admin)

    request = rf.get("/")
    request.user = admin
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category.id
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "DraftTeam" in content
    assert "черновик" in content


@pytest.mark.django_db
def test_protocol_view_filters_rows_by_category(rf, django_user_model):
    race = _make_race()
    category_a = _make_category(race, code="a", short_name="A", name="Категория A")
    category_b = _make_category(
        race, code="b", short_name="B", name="Категория B", order=1
    )
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category_a, teamname="TeamA", finish_time=2000)
    _make_started_team(owner, category_b, teamname="TeamB", finish_time=2000)

    build_protocol(race, None)
    freeze_protocol(race)

    request = rf.get("/")
    request.user = AnonymousUser()
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category_a.id
    )
    content = response.content.decode()
    assert "TeamA" in content
    assert "TeamB" not in content


@pytest.mark.django_db
def test_protocol_view_title_matches_status(rf, django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category, teamname="A", finish_time=2000)

    admin = django_user_model.objects.create_user(username="radmin2", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)

    build_protocol(race, admin)
    request = rf.get("/")
    request.user = admin
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category.id
    )
    assert "Предварительный протокол" in response.content.decode()

    freeze_protocol(race)
    request = rf.get("/")
    request.user = AnonymousUser()
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category.id
    )
    assert "Итоговый протокол" in response.content.decode()


@pytest.mark.django_db
def test_protocol_view_immutability_guarantee(rf, django_user_model):
    """A frozen snapshot is unaffected by later edits to live Team/Mark data."""
    race = _make_race()
    category = _make_category(race)
    cp1 = _make_checkpoint(race, 1, 10)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    team = _make_started_team(owner, category, teamname="Original", finish_time=2000)

    build_protocol(race, None)
    freeze_protocol(race)

    # Mutate live data after the snapshot was frozen.
    team.teamname = "Changed"
    team.save()
    _make_mark(team, cp1)

    request = rf.get("/")
    request.user = AnonymousUser()
    response = ProtocolView.as_view()(
        request, race_slug=race.slug, category_id=category.id
    )
    content = response.content.decode()
    assert "Original" in content
    assert "Changed" not in content


# ---------------------------------------------------------------------------
# ProtocolBuildView / ProtocolFreezeView (Task 4)
# ---------------------------------------------------------------------------
# The URL for these views is wired in Task 5, so these tests call them
# directly via RequestFactory instead of ``client``/``reverse()``.


@pytest.mark.django_db
def test_protocol_build_forbidden_for_non_admin(rf, django_user_model):
    race = _make_race()
    _make_category(race)
    other = django_user_model.objects.create_user(username="other", password="x")

    request = _attach_messages(rf.post("/"))
    request.user = other
    response = ProtocolBuildView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 403
    assert not Protocol.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_protocol_freeze_forbidden_for_non_admin(rf, django_user_model):
    race = _make_race()
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="owner", password="x")
    _make_started_team(owner, category, teamname="A", finish_time=2000)
    protocol = build_protocol(race, None)
    other = django_user_model.objects.create_user(username="other2", password="x")

    request = _attach_messages(rf.post("/"))
    request.user = other
    response = ProtocolFreezeView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 403
    protocol.refresh_from_db()
    assert protocol.status == Protocol.DRAFT


@pytest.mark.django_db
def test_protocol_build_and_freeze_admin_flow(rf, django_user_model):
    race = _make_race()
    category = _make_category(race)
    admin = django_user_model.objects.create_user(username="radmin3", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    owner = django_user_model.objects.create_user(username="owner3", password="x")
    _make_started_team(owner, category, teamname="A", finish_time=2000)

    request = _attach_messages(rf.post("/"))
    request.user = admin
    response = ProtocolBuildView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 302
    protocol = Protocol.objects.get(race=race)
    assert protocol.status == Protocol.DRAFT

    request = _attach_messages(rf.post("/"))
    request.user = admin
    response = ProtocolFreezeView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 302
    protocol.refresh_from_db()
    assert protocol.status == Protocol.FINAL
    assert protocol.frozen_at is not None

    # A build after freeze creates a *new* draft; the final stays untouched.
    request = _attach_messages(rf.post("/"))
    request.user = admin
    ProtocolBuildView.as_view()(request, race_slug=race.slug)
    assert Protocol.objects.filter(race=race).count() == 2
    protocol.refresh_from_db()
    assert protocol.status == Protocol.FINAL


@pytest.mark.django_db
def test_protocol_freeze_without_draft_is_friendly(rf, django_user_model):
    race = _make_race()
    _make_category(race)
    admin = django_user_model.objects.create_user(username="radmin4", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)

    request = _attach_messages(rf.post("/"))
    request.user = admin
    response = ProtocolFreezeView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 302
    assert not Protocol.objects.filter(race=race).exists()


@pytest.mark.django_db
def test_protocol_build_redirects_to_referer_when_safe(rf, django_user_model):
    race = _make_race()
    _make_category(race)
    admin = django_user_model.objects.create_user(username="radmin5", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)

    request = _attach_messages(
        rf.post("/", HTTP_REFERER="http://testserver/race/some/results/")
    )
    request.user = admin
    response = ProtocolBuildView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 302
    assert response.url == "http://testserver/race/some/results/"


@pytest.mark.django_db
def test_protocol_build_ignores_offsite_referer(rf, django_user_model):
    race = _make_race()
    category = _make_category(race)
    admin = django_user_model.objects.create_user(username="radmin6", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)

    request = _attach_messages(rf.post("/", HTTP_REFERER="http://evil.example/"))
    request.user = admin
    response = ProtocolBuildView.as_view()(request, race_slug=race.slug)
    assert response.status_code == 302
    assert "evil.example" not in response.url
    assert (
        reverse(
            "category_results",
            kwargs={"race_slug": race.slug, "category_id": category.id},
        )
        in response.url
    )


@pytest.mark.django_db
def test_protocol_build_falls_back_to_race_page_without_active_category(
    rf, django_user_model
):
    race = _make_race()
    # No category at all -> _protocol_redirect_back has nowhere else to go.
    admin = django_user_model.objects.create_user(username="radmin7", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)

    request = _attach_messages(rf.post("/"))
    request.user = admin
    response = ProtocolBuildView.as_view()(request, race_slug=race.slug)

    assert response.status_code == 302
    assert response.url == reverse("race", kwargs={"race_slug": race.slug})


# ---------------------------------------------------------------------------
# URL routing (Task 5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_category_results_url_resolves_to_protocol_view(client):
    race = _make_race(slug="url-routing-race")
    category = _make_category(race)

    resp = client.get(reverse("category_results", args=[race.slug, category.id]))

    assert resp.status_code == 200
    assert resp.resolver_match.func.view_class is ProtocolView
    assert "race/protocol.html" in [t.name for t in resp.templates]


@pytest.mark.django_db
def test_category_results_deprecated_url_renders_old_view(client, django_user_model):
    race = _make_race(slug="url-routing-race-2")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="deprowner", password="x")
    _make_started_team(owner, category, finish_time=2000)

    resp = client.get(
        reverse("category_results_deprecated", args=[race.slug, category.id])
    )

    assert resp.status_code == 200
    assert "teams_result.html" in [t.name for t in resp.templates]


@pytest.mark.django_db
def test_protocol_build_and_freeze_url_names_resolve(client, django_user_model):
    race = _make_race(slug="url-routing-race-3")
    _make_category(race)
    admin = django_user_model.objects.create_user(username="urladmin", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)

    build_url = reverse("protocol_build", kwargs={"race_slug": race.slug})
    freeze_url = reverse("protocol_freeze", kwargs={"race_slug": race.slug})

    resp = client.post(build_url)
    assert resp.status_code == 302
    assert Protocol.objects.filter(race=race, status=Protocol.DRAFT).exists()

    resp = client.post(freeze_url)
    assert resp.status_code == 302
    assert Protocol.objects.filter(race=race, status=Protocol.FINAL).exists()


@pytest.mark.django_db
def test_race_id_redirect_still_works_for_results_url(client):
    race = _make_race(slug="url-routing-race-4")
    category = _make_category(race)

    resp = client.get(f"/race/{race.id}/category/{category.id}/results/")

    assert resp.status_code == 301
    assert resp["Location"] == f"/race/{race.slug}/category/{category.id}/results/"


# --- RaceMapPositionsView (Task 3) -----------------------------------------


def _make_track_point(team, race, point_id, **kwargs):
    defaults = {
        "install_id": "install-1",
        "segment_id": "seg-1",
        "lat": 55.0,
        "lon": 37.0,
        "accuracy": 5.0,
        "gps_time_ms": 1_700_000_000_000,
        "elapsed_at": 1000,
    }
    defaults.update(kwargs)
    return TrackPoint.objects.create(id=point_id, team=team, race=race, **defaults)


@pytest.mark.django_db
def test_race_map_positions_anonymous_redirects_to_login(client):
    race = _make_race(slug="map-pos-anon")

    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 302
    assert reverse("login") in resp.url


@pytest.mark.django_db
def test_race_map_positions_regular_user_forbidden(client, django_user_model):
    race = _make_race(slug="map-pos-forbidden")
    user = django_user_model.objects.create_user(username="plain-map", password="x")
    client.force_login(user)

    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_map_positions_denies_unassigned_superuser_allows_admin(
    client, django_user_model
):
    race = _make_race(slug="map-pos-admins")

    superuser = django_user_model.objects.create_superuser(
        username="map-su", password="x", email="map-su@example.com"
    )
    client.force_login(superuser)
    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403
    client.logout()

    admin = django_user_model.objects.create_user(username="map-admin", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_race_map_positions_returns_latest_point_per_team(client, django_user_model):
    race = _make_race(slug="map-pos-latest")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="map-owner", password="x")
    team = _make_team(owner, category, teamname="Alpha", start_number="7")

    _make_track_point(team, race, "tp-1", gps_time_ms=1_700_000_000_000, lat=10.0)
    _make_track_point(team, race, "tp-2", gps_time_ms=1_700_000_030_000, lat=20.0)
    _make_track_point(team, race, "tp-3", gps_time_ms=1_700_000_010_000, lat=15.0)

    admin = django_user_model.objects.create_superuser(
        username="map-latest-su", password="x", email="map-latest-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["team_id"] == team.id
    assert row["name"] == "Alpha"
    assert row["number"] == "7"
    assert row["lat"] == 20.0
    assert row["gps_time_ms"] == 1_700_000_030_000
    assert row["received_at"] is not None
    assert row["install_id"] == "install-1"
    assert row["segment_id"] == "seg-1"


@pytest.mark.django_db
def test_race_map_positions_team_without_points_has_null_fields(
    client, django_user_model
):
    race = _make_race(slug="map-pos-nodata")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-nodata-owner", password="x"
    )
    _make_team(owner, category, teamname="NoTrack", start_number="9")

    admin = django_user_model.objects.create_superuser(
        username="map-nodata-su", password="x", email="map-nodata-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["lat"] is None
    assert row["lon"] is None
    assert row["gps_time_ms"] is None
    assert row["received_at"] is None
    assert row["install_id"] is None
    assert row["segment_id"] is None


@pytest.mark.django_db
def test_race_map_positions_excludes_other_race_points(client, django_user_model):
    race = _make_race(slug="map-pos-thisrace")
    other_race = _make_race(slug="map-pos-otherrace")
    category = _make_category(race)
    other_category = _make_category(other_race, code="other")
    owner = django_user_model.objects.create_user(
        username="map-cross-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Home", start_number="1")
    other_team = _make_team(owner, other_category, teamname="Away", start_number="2")

    _make_track_point(team, race, "tp-home", lat=1.0, lon=2.0)
    _make_track_point(other_team, other_race, "tp-away", lat=3.0, lon=4.0)

    admin = django_user_model.objects.create_superuser(
        username="map-cross-su", password="x", email="map-cross-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_positions", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    rows = resp.json()
    team_ids = [row["team_id"] for row in rows]
    assert team.id in team_ids
    assert other_team.id not in team_ids


@pytest.mark.django_db
def test_race_map_positions_tie_breaker_is_deterministic(client, django_user_model):
    race = _make_race(slug="map-pos-tie")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-tie-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Tie", start_number="3")

    _make_track_point(team, race, "tp-tie-1", gps_time_ms=1_700_000_000_000, lat=1.0)
    _make_track_point(team, race, "tp-tie-2", gps_time_ms=1_700_000_000_000, lat=2.0)

    admin = django_user_model.objects.create_superuser(
        username="map-tie-su", password="x", email="map-tie-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    url = reverse("race_map_positions", kwargs={"race_slug": race.slug})

    first = client.get(url).json()[0]["lat"]
    second = client.get(url).json()[0]["lat"]

    assert first == second


def test_race_map_positions_url_resolves():
    resolved = resolve("/race/some-slug/map/positions/")
    assert resolved.func.view_class is RaceMapPositionsView


# --- RaceMapTrackView (Task 4) ----------------------------------------------


@pytest.mark.django_db
def test_race_map_track_anonymous_redirects_to_login(client, django_user_model):
    race = _make_race(slug="map-track-anon")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-anon-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Anon", start_number="1")

    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 302
    assert reverse("login") in resp.url


@pytest.mark.django_db
def test_race_map_track_regular_user_forbidden(client, django_user_model):
    race = _make_race(slug="map-track-forbidden")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-forbidden-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Forbidden", start_number="1")
    user = django_user_model.objects.create_user(
        username="map-track-plain", password="x"
    )
    client.force_login(user)

    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_map_track_other_race_team_404(client, django_user_model):
    race = _make_race(slug="map-track-thisrace")
    other_race = _make_race(slug="map-track-otherrace")
    other_category = _make_category(other_race, code="other")
    owner = django_user_model.objects.create_user(
        username="map-track-cross-owner", password="x"
    )
    other_team = _make_team(owner, other_category, teamname="Away", start_number="2")

    admin = django_user_model.objects.create_superuser(
        username="map-track-cross-su",
        password="x",
        email="map-track-cross-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(
        reverse(
            "race_map_track",
            kwargs={"race_slug": race.slug, "team_id": other_team.id},
        )
    )

    assert resp.status_code == 404


@pytest.mark.django_db
def test_race_map_track_thins_points_within_30s(client, django_user_model):
    race = _make_race(slug="map-track-thin")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-thin-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Thin", start_number="1")

    base = 1_700_000_000_000
    # 10s apart: 0, 10s, 20s, 30s, 40s -> kept: 0, 30s, 40s (last always kept)
    for i in range(5):
        _make_track_point(
            team,
            race,
            f"tp-thin-{i}",
            gps_time_ms=base + i * 10_000,
            lat=float(i),
            lon=float(i),
        )

    admin = django_user_model.objects.create_superuser(
        username="map-track-thin-su",
        password="x",
        email="map-track-thin-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["segments"]) == 1
    segment = data["segments"][0]
    assert segment["points"] == [[0.0, 0.0], [3.0, 3.0], [4.0, 4.0]]


@pytest.mark.django_db
def test_race_map_track_two_segment_ids_ordered_by_time(client, django_user_model):
    race = _make_race(slug="map-track-twoseg")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-twoseg-owner", password="x"
    )
    team = _make_team(owner, category, teamname="TwoSeg", start_number="1")

    base = 1_700_000_000_000
    _make_track_point(
        team,
        race,
        "tp-seg2-a",
        segment_id="seg-2",
        gps_time_ms=base + 100_000,
        lat=20.0,
        lon=20.0,
    )
    _make_track_point(
        team,
        race,
        "tp-seg1-a",
        segment_id="seg-1",
        gps_time_ms=base,
        lat=10.0,
        lon=10.0,
    )

    admin = django_user_model.objects.create_superuser(
        username="map-track-twoseg-su",
        password="x",
        email="map-track-twoseg-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 200
    segments = resp.json()["segments"]
    assert len(segments) == 2
    assert segments[0]["segment_id"] == "seg-1"
    assert segments[0]["points"] == [[10.0, 10.0]]
    assert segments[1]["segment_id"] == "seg-2"
    assert segments[1]["points"] == [[20.0, 20.0]]


@pytest.mark.django_db
def test_race_map_track_same_segment_id_two_installs_is_two_segments(
    client, django_user_model
):
    race = _make_race(slug="map-track-twophone")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-twophone-owner", password="x"
    )
    team = _make_team(owner, category, teamname="TwoPhone", start_number="1")

    base = 1_700_000_000_000
    _make_track_point(
        team,
        race,
        "tp-phone1",
        install_id="phone-1",
        segment_id="seg-shared",
        gps_time_ms=base,
        lat=1.0,
        lon=1.0,
    )
    _make_track_point(
        team,
        race,
        "tp-phone2",
        install_id="phone-2",
        segment_id="seg-shared",
        gps_time_ms=base + 1_000,
        lat=2.0,
        lon=2.0,
    )

    admin = django_user_model.objects.create_superuser(
        username="map-track-twophone-su",
        password="x",
        email="map-track-twophone-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 200
    segments = resp.json()["segments"]
    assert len(segments) == 2
    install_ids = {segment["install_id"] for segment in segments}
    assert install_ids == {"phone-1", "phone-2"}
    all_points = segments[0]["points"] + segments[1]["points"]
    assert [1.0, 1.0] in all_points
    assert [2.0, 2.0] in all_points


@pytest.mark.django_db
def test_race_map_track_points_ordered_by_gps_time_within_segment(
    client, django_user_model
):
    race = _make_race(slug="map-track-order")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-order-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Order", start_number="1")

    base = 1_700_000_000_000
    _make_track_point(
        team, race, "tp-order-2", gps_time_ms=base + 60_000, lat=2.0, lon=2.0
    )
    _make_track_point(team, race, "tp-order-1", gps_time_ms=base, lat=1.0, lon=1.0)

    admin = django_user_model.objects.create_superuser(
        username="map-track-order-su",
        password="x",
        email="map-track-order-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 200
    segment = resp.json()["segments"][0]
    assert segment["points"] == [[1.0, 1.0], [2.0, 2.0]]


@pytest.mark.django_db
def test_race_map_track_keeps_true_last_point_on_tied_gps_time(
    client, django_user_model
):
    from django.utils import timezone as django_timezone

    race = _make_race(slug="map-track-tie")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="map-track-tie-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Tie", start_number="1")

    base = 1_700_000_000_000
    _make_track_point(team, race, "tp-tie-0", gps_time_ms=base, lat=0.0, lon=0.0)
    _make_track_point(
        team, race, "tp-tie-30a", gps_time_ms=base + 30_000, lat=1.0, lon=1.0
    )
    _make_track_point(
        team, race, "tp-tie-30b", gps_time_ms=base + 30_000, lat=2.0, lon=2.0
    )
    # Force tp-tie-30a to sort before tp-tie-30b on the created_at tie-breaker,
    # so the two tied-gps_time_ms points have a deterministic, distinct order
    # and tp-tie-30b (with different coordinates) is the true last point.
    TrackPoint.objects.filter(id="tp-tie-30a").update(
        created_at=django_timezone.now() - datetime.timedelta(seconds=1)
    )

    admin = django_user_model.objects.create_superuser(
        username="map-track-tie-su",
        password="x",
        email="map-track-tie-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": team.id})
    )

    assert resp.status_code == 200
    segment = resp.json()["segments"][0]
    # The thinning pick (tp-tie-30a) and the true final point (tp-tie-30b,
    # different coordinates despite the tied gps_time_ms) must both survive —
    # comparing only the timestamp would wrongly treat them as the same point
    # and drop the segment's real last fix.
    assert segment["points"] == [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]


def test_race_map_track_url_resolves():
    resolved = resolve("/race/some-slug/map/track/7/")
    assert resolved.func.view_class is RaceMapTrackView


# --- RaceMapView (Task 5) ----------------------------------------------------


@pytest.mark.django_db
def test_race_map_page_anonymous_redirects_to_login(client):
    race = _make_race(slug="map-page-anon")

    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 302
    assert reverse("login") in resp.url


@pytest.mark.django_db
def test_race_map_page_regular_user_forbidden(client, django_user_model):
    race = _make_race(slug="map-page-forbidden")
    user = django_user_model.objects.create_user(
        username="map-page-plain", password="x"
    )
    client.force_login(user)

    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_map_page_denies_unassigned_superuser_allows_admin(
    client, django_user_model
):
    race = _make_race(slug="map-page-admins")

    superuser = django_user_model.objects.create_superuser(
        username="map-page-su", password="x", email="map-page-su@example.com"
    )
    client.force_login(superuser)
    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403
    client.logout()

    admin = django_user_model.objects.create_user(
        username="map-page-admin", password="x"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_race_map_page_config_island_has_both_urls(client, django_user_model):
    race = _make_race(slug="map-page-config")
    superuser = django_user_model.objects.create_superuser(
        username="map-page-config-su",
        password="x",
        email="map-page-config-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)

    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    config = _script_json(resp.content.decode(), "raceMapConfig")
    assert config["positionsUrl"] == reverse(
        "race_map_positions", kwargs={"race_slug": race.slug}
    )
    assert config["trackUrlTemplate"] == re.sub(
        r"/0/$",
        "/{team_id}/",
        reverse("race_map_track", kwargs={"race_slug": race.slug, "team_id": 0}),
    )


def test_race_map_page_config_island_handles_numeric_zero_slug(
    client, django_user_model
):
    """A race slug of exactly "0" must not corrupt trackUrlTemplate.

    The template is built by rendering the track URL with a team_id=0
    placeholder and substituting it back out; a naive str.replace("/0/", ...)
    would also match the "/0/" from the slug segment itself.
    """
    race = _make_race(slug="0")
    superuser = django_user_model.objects.create_superuser(
        username="map-page-config-zero-su",
        password="x",
        email="map-page-config-zero-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)

    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    config = _script_json(resp.content.decode(), "raceMapConfig")
    assert config["trackUrlTemplate"] == "/race/0/map/track/{team_id}/"


def test_race_map_page_url_resolves():
    resolved = resolve("/race/some-slug/map/")
    assert resolved.func.view_class is RaceMapView


# --- RaceMapMarksView (map layer «Взятия КП») --------------------------------


def _make_located_mark(team, checkpoint, lat=55.0, lon=37.0, **kwargs):
    """A ``Mark`` with a GPS fix, for the marks map-layer tests."""
    mark = _make_mark(team, checkpoint, **kwargs)
    mark.loc_lat = lat
    mark.loc_lon = lon
    mark.loc_accuracy = 8.5
    mark.save()
    return mark


@pytest.mark.django_db
def test_race_map_marks_anonymous_redirects_to_login(client):
    race = _make_race(slug="map-marks-anon")

    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 302
    assert reverse("login") in resp.url


@pytest.mark.django_db
def test_race_map_marks_regular_user_forbidden(client, django_user_model):
    race = _make_race(slug="map-marks-forbidden")
    user = django_user_model.objects.create_user(username="marks-plain", password="x")
    client.force_login(user)

    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 403


@pytest.mark.django_db
def test_race_map_marks_denies_unassigned_superuser_allows_admin(
    client, django_user_model
):
    race = _make_race(slug="map-marks-admins")

    superuser = django_user_model.objects.create_superuser(
        username="marks-su", password="x", email="marks-su@example.com"
    )
    client.force_login(superuser)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403
    client.logout()

    admin = django_user_model.objects.create_user(username="marks-admin", password="x")
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_race_map_marks_row_shape(client, django_user_model):
    race = _make_race(slug="map-marks-shape")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(username="marks-owner", password="x")
    team = _make_team(owner, category, teamname="Alpha", start_number="7")
    cp = _make_checkpoint(race, 5, 10)
    mark = _make_located_mark(
        team, cp, lat=55.5, lon=37.5, wall_ms=1_700_000_000_000, verified=True
    )

    admin = django_user_model.objects.create_superuser(
        username="marks-shape-su", password="x", email="marks-shape-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row == {
        "mark_id": mark.id,
        "team_id": team.id,
        "team_name": "Alpha",
        "team_number": "7",
        "checkpoint_id": cp.id,
        "cp_number": 5,
        "lat": 55.5,
        "lon": 37.5,
        "accuracy": 8.5,
        "verified": True,
        "method": "nfc",
        "time_ms": 1_700_000_000_000,
    }


@pytest.mark.django_db
def test_race_map_marks_excludes_marks_without_coordinates(client, django_user_model):
    race = _make_race(slug="map-marks-noloc")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="marks-noloc-owner", password="x"
    )
    team = _make_team(owner, category)
    cp = _make_checkpoint(race, 1, 10)
    _make_mark(team, cp)  # no loc_* fields
    located = _make_located_mark(team, cp)

    admin = django_user_model.objects.create_superuser(
        username="marks-noloc-su", password="x", email="marks-noloc-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    rows = resp.json()
    assert [row["mark_id"] for row in rows] == [located.id]


@pytest.mark.django_db
def test_race_map_marks_unknown_checkpoint_has_null_cp_number(
    client, django_user_model
):
    race = _make_race(slug="map-marks-orphan")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="marks-orphan-owner", password="x"
    )
    team = _make_team(owner, category)
    _make_located_mark(team, 999_999, verified=False)

    admin = django_user_model.objects.create_superuser(
        username="marks-orphan-su", password="x", email="marks-orphan-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["checkpoint_id"] == 999_999
    assert rows[0]["cp_number"] is None
    assert rows[0]["verified"] is False


@pytest.mark.django_db
def test_race_map_marks_excludes_other_race_marks(client, django_user_model):
    race = _make_race(slug="map-marks-thisrace")
    other_race = _make_race(slug="map-marks-otherrace")
    category = _make_category(race)
    other_category = _make_category(other_race, code="other")
    owner = django_user_model.objects.create_user(
        username="marks-cross-owner", password="x"
    )
    team = _make_team(owner, category, teamname="Home")
    other_team = _make_team(owner, other_category, teamname="Away")
    home_mark = _make_located_mark(team, _make_checkpoint(race, 1, 10))
    _make_located_mark(other_team, _make_checkpoint(other_race, 1, 10))

    admin = django_user_model.objects.create_superuser(
        username="marks-cross-su", password="x", email="marks-cross-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    rows = resp.json()
    assert [row["mark_id"] for row in rows] == [home_mark.id]


@pytest.mark.django_db
def test_race_map_marks_time_ms_prefers_trusted_ms(client, django_user_model):
    race = _make_race(slug="map-marks-time")
    category = _make_category(race)
    owner = django_user_model.objects.create_user(
        username="marks-time-owner", password="x"
    )
    team = _make_team(owner, category)
    cp = _make_checkpoint(race, 1, 10)
    with_trusted = _make_located_mark(
        team, cp, wall_ms=100, trusted_ms=1_700_000_000_000
    )
    wall_only = _make_located_mark(team, cp, wall_ms=200, trusted_ms=None)

    admin = django_user_model.objects.create_superuser(
        username="marks-time-su", password="x", email="marks-time-su@example.com"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)
    resp = client.get(reverse("race_map_marks", kwargs={"race_slug": race.slug}))

    by_id = {row["mark_id"]: row for row in resp.json()}
    assert by_id[with_trusted.id]["time_ms"] == 1_700_000_000_000
    assert by_id[wall_only.id]["time_ms"] == 200


def test_race_map_marks_url_resolves():
    resolved = resolve("/race/some-slug/map/marks/")
    assert resolved.func.view_class is RaceMapMarksView


@pytest.mark.django_db
def test_race_map_page_config_island_has_marks_url(client, django_user_model):
    race = _make_race(slug="map-page-marks-config")
    superuser = django_user_model.objects.create_superuser(
        username="map-page-marks-su",
        password="x",
        email="map-page-marks-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)

    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    config = _script_json(resp.content.decode(), "raceMapConfig")
    assert config["marksUrl"] == reverse(
        "race_map_marks", kwargs={"race_slug": race.slug}
    )


@pytest.mark.django_db
def test_race_map_page_config_island_has_tile_urls(client, django_user_model):
    race = _make_race(slug="map-page-tiles-config")
    superuser = django_user_model.objects.create_superuser(
        username="map-page-tiles-su",
        password="x",
        email="map-page-tiles-su@example.com",
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)

    resp = client.get(reverse("race_map", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    config = _script_json(resp.content.decode(), "raceMapConfig")
    assert config["tileUrls"] == {
        "osm": settings.MAP_TILE_URL_OSM,
        "topo": settings.MAP_TILE_URL_TOPO,
    }


# --- RaceAppDataView / RaceAppDataTeamView (app-data pages) -----------------


def _make_app_data_race(slug):
    """Race + category + admin-owned team with boundary checkpoints."""
    race = _make_race(slug=slug)
    race.is_published = True
    race.save()
    category = _make_category(race)
    cp_start = Checkpoint.objects.create(race=race, number=100, cost=0, type="start")
    cp_finish = Checkpoint.objects.create(race=race, number=200, cost=0, type="finish")
    cp_kp = _make_checkpoint(race, 1, 10)
    return race, category, cp_start, cp_finish, cp_kp


@pytest.mark.django_db
def test_app_data_anonymous_redirects_to_login(client):
    race = _make_race(slug="app-data-anon")

    resp = client.get(reverse("race_app_data", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 302
    assert reverse("login") in resp.url


@pytest.mark.django_db
def test_app_data_regular_user_forbidden(client, django_user_model):
    race = _make_race(slug="app-data-forbidden")
    user = django_user_model.objects.create_user(username="plain-appdata", password="x")
    client.force_login(user)

    assert (
        client.get(
            reverse("race_app_data", kwargs={"race_slug": race.slug})
        ).status_code
        == 403
    )
    assert (
        client.get(
            reverse(
                "race_app_data_team",
                kwargs={"race_slug": race.slug, "team_id": 1},
            )
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_app_data_race_admin_200(client, django_user_model):
    race, category, *_ = _make_app_data_race("app-data-admin")
    owner = django_user_model.objects.create_user(
        username="appdata-owner", password="x"
    )
    _make_team(owner, category, teamname="Ромашки")

    admin = django_user_model.objects.create_user(
        username="appdata-admin", password="x"
    )
    RaceAdmin.objects.create(race=race, user=admin, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin)

    resp = client.get(reverse("race_app_data", kwargs={"race_slug": race.slug}))

    assert resp.status_code == 200
    assert "Ромашки" in resp.content.decode()


@pytest.mark.django_db
def test_app_data_team_404_for_team_not_in_race(client, django_user_model):
    race, *_ = _make_app_data_race("app-data-404")
    other_race = _make_race(slug="app-data-404-other")
    other_category = _make_category(other_race)
    owner = django_user_model.objects.create_user(username="appdata-404", password="x")
    foreign_team = _make_team(owner, other_category)

    superuser = django_user_model.objects.create_superuser(
        username="appdata-404-su", password="x", email="ad404@example.com"
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)

    resp = client.get(
        reverse(
            "race_app_data_team",
            kwargs={"race_slug": race.slug, "team_id": foreign_team.id},
        )
    )

    assert resp.status_code == 404


@pytest.mark.django_db
def test_app_data_overview_chips_takes_and_judge_scans(django_user_model):
    from website.models.tag import Tag

    race, category, cp_start, cp_finish, cp_kp = _make_app_data_race("app-data-rows")
    owner = django_user_model.objects.create_user(username="appdata-rows", password="x")
    take_ms = 1_700_000_100_000
    team = _make_team(owner, category, teamname="A", start_time=take_ms, finish_time=0)

    Tag.objects.create(number=5, nfc_uid="AA:01")
    # Chip uid stored raw/lowercase in MarkPresent — must still resolve to №5.
    _make_mark(team, cp_start, present_chips=("aa:01", "FF:99"), wall_ms=take_ms)
    _make_mark(team, cp_kp, present_chips=("aa:01",), wall_ms=take_ms + 60_000)

    JudgeScan.objects.create(
        id="js-1",
        race=race,
        source_install_id="judge-phone",
        event_type="start",
        participant_number=5,
        nfc_uid="AA:01",
        wall_ms=take_ms - 120_000,
    )
    JudgeScan.objects.create(
        id="js-2",
        race=race,
        source_install_id="judge-phone",
        event_type="start",
        participant_number=77,
        nfc_uid="DE:AD",
        wall_ms=take_ms - 60_000,
    )

    context = build_overview(race)

    assert len(context["rows"]) == 1
    row = context["rows"][0]
    labels = {chip["label"] for chip in row["chips"]}
    assert "№5" in labels
    assert "FF:99" in labels  # not in the Tag pool — raw uid, flagged
    assert row["take_start"] == format_ms(take_ms)
    assert row["take_finish"] == ""
    assert row["start_mismatch"] is False
    assert row["judge_start"] == {
        "scanned": 1,
        "chips": 2,
        "first": format_ms(take_ms - 120_000),
        "last": format_ms(take_ms - 120_000),
        "spread": False,
    }
    assert row["judge_finish"] is None
    assert row["marks_total"] == 2
    assert row["marks_verified"] == 2

    assert len(context["unmatched_scans"]) == 1
    assert context["unmatched_scans"][0]["participant_number"] == 77


@pytest.mark.django_db
def test_app_data_overview_flags_boundary_mismatch(django_user_model):
    race, category, cp_start, *_ = _make_app_data_race("app-data-mismatch")
    owner = django_user_model.objects.create_user(username="appdata-mm", password="x")
    take_ms = 1_700_000_100_000
    # Stored Team.start_time diverges from the earliest verified take.
    team = _make_team(owner, category, start_time=take_ms + 5_000)
    _make_mark(team, cp_start, wall_ms=take_ms)

    row = build_overview(race)["rows"][0]

    assert row["start_mismatch"] is True
    # Unverified marks must not count as a boundary take.
    Mark.objects.all().delete()
    _make_mark(team, cp_start, verified=False, wall_ms=take_ms)
    row = build_overview(race)["rows"][0]
    assert row["take_start"] == ""
    assert row["start_mismatch"] is False


@pytest.mark.django_db
def test_app_data_team_timeline_orders_all_event_kinds(client, django_user_model):
    from website.models.tag import Tag

    race, category, cp_start, cp_finish, cp_kp = _make_app_data_race("app-data-feed")
    owner = django_user_model.objects.create_user(username="appdata-feed", password="x")
    base_ms = 1_700_000_000_000
    team = _make_team(
        owner, category, teamname="Лента", start_time=base_ms, finish_time=0
    )
    Tag.objects.create(number=9, nfc_uid="AB:CD")

    mark = _make_mark(team, cp_kp, present_chips=("ab:cd",), wall_ms=base_ms + 300_000)
    # Sentinel present slot: counted but unsnapshotted member.
    MarkPresent.objects.create(
        mark=mark, nfc_uid=None, code=None, number=0, number_in_team=2
    )
    MarkPhoto.objects.create(mark=mark, frame_id="f1", image="mark_photos/m/f1.jpg")
    # Unknown КП: checkpoint_id that is not in the race legend.
    _make_mark(team, 999_999, present_chips=("ab:cd",), wall_ms=base_ms + 400_000)

    JudgeScan.objects.create(
        id="js-feed-1",
        race=race,
        source_install_id="judge-phone",
        event_type="start",
        participant_number=9,
        nfc_uid="ab:cd ",  # raw — normalized on read before chip matching
        wall_ms=base_ms - 60_000,
    )
    _make_track_point(
        team, race, "tp-feed-1", gps_time_ms=base_ms + 100_000, segment_id="seg-a"
    )
    _make_track_point(
        team, race, "tp-feed-2", gps_time_ms=base_ms + 200_000, segment_id="seg-a"
    )

    context = build_team_timeline(race, team)

    kinds = [event["kind"] for event in context["events"]]
    assert kinds == ["judge", "boundary", "track", "mark", "mark"]
    assert [event["ms"] for event in context["events"]] == sorted(
        event["ms"] for event in context["events"]
    )

    mark_event = context["events"][3]
    assert mark_event["cp_number"] == cp_kp.number
    present_labels = [p["label"] for p in mark_event["present"]]
    assert present_labels == ["№9", "без снимка"]
    assert mark_event["photos"] == ["/media/mark_photos/m/f1.jpg"]

    unknown_event = context["events"][4]
    assert unknown_event["cp_unknown"] is True
    assert unknown_event["checkpoint_id"] == 999_999

    track_event = context["events"][2]
    assert track_event["points"] == 2
    assert track_event["segment_id"] == "seg-a"

    assert [chip["label"] for chip in context["chips"]] == ["№9"]
    assert context["marks_count"] == 2
    assert context["photos_count"] == 1

    # The rendered page must not blow up on the full fixture set.
    superuser = django_user_model.objects.create_superuser(
        username="appdata-feed-su", password="x", email="adfeed@example.com"
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)
    resp = client.get(
        reverse(
            "race_app_data_team",
            kwargs={"race_slug": race.slug, "team_id": team.id},
        )
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "без снимка" in body
    assert "/media/mark_photos/m/f1.jpg" in body


@pytest.mark.django_db
def test_app_data_team_timeline_flags_clock_skew(client, django_user_model):
    """A wall-vs-trusted divergence over the threshold gets a «часы …» badge."""
    race, category, cp_start, cp_finish, cp_kp = _make_app_data_race("app-data-skew")
    owner = django_user_model.objects.create_user(username="appdata-skew", password="x")
    base_ms = 1_700_000_000_000
    team = _make_team(owner, category, teamname="Часы")

    # Phone clock 5 min ahead of trusted time — badge expected.
    _make_mark(
        team,
        cp_kp,
        present_chips=("sk:01",),
        wall_ms=base_ms + 300_000,
        trusted_ms=base_ms,
    )
    # 10 s divergence — under the threshold, no badge.
    _make_mark(
        team,
        cp_kp,
        present_chips=("sk:01",),
        wall_ms=base_ms + 1_010_000,
        trusted_ms=base_ms + 1_000_000,
    )
    # No trusted_ms at all — nothing to compare, no badge.
    _make_mark(team, cp_kp, present_chips=("sk:01",), wall_ms=base_ms + 2_000_000)
    # Judge scan with the phone clock 2 min behind.
    JudgeScan.objects.create(
        id="js-skew-1",
        race=race,
        source_install_id="judge-phone",
        event_type="start",
        participant_number=1,
        nfc_uid="SK:01",
        wall_ms=base_ms + 3_000_000 - 120_000,
        trusted_ms=base_ms + 3_000_000,
    )

    context = build_team_timeline(race, team)

    skews = {
        (event["kind"], event["ms"]): event["clock_skew"]
        for event in context["events"]
        if event["kind"] in ("mark", "judge")
    }
    skewed_mark = skews[("mark", base_ms)]
    assert skewed_mark["label"] == "часы спешат на 5м 00с"
    assert skewed_mark["wall"] == format_ms(base_ms + 300_000)
    assert skewed_mark["trusted"] == format_ms(base_ms)
    assert skews[("mark", base_ms + 1_000_000)] is None
    assert skews[("mark", base_ms + 2_000_000)] is None

    judge_skew = skews[("judge", base_ms + 3_000_000)]
    assert judge_skew["label"] == "часы отстают на 2м 00с"

    # The rendered page shows the badge.
    superuser = django_user_model.objects.create_superuser(
        username="appdata-skew-su", password="x", email="adskew@example.com"
    )
    RaceAdmin.objects.create(race=race, user=superuser, role=RaceAdmin.Role.ADMIN)
    client.force_login(superuser)
    resp = client.get(
        reverse(
            "race_app_data_team",
            kwargs={"race_slug": race.slug, "team_id": team.id},
        )
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "часы спешат на 5м 00с" in body
    assert "часы отстают на 2м 00с" in body


def test_app_data_urls_resolve():
    assert resolve("/race/some-slug/app-data/").func.view_class is RaceAppDataView
    assert (
        resolve("/race/some-slug/app-data/team/7/").func.view_class
        is RaceAppDataTeamView
    )


# --- Promo codes: model ---

from apps.race.models import RacePromo  # noqa: E402


def _promo(race, code="SALE", discount_type=RacePromo.PERCENT, value=40, **kwargs):
    return RacePromo.objects.create(
        race=race, code=code, discount_type=discount_type, value=value, **kwargs
    )


@pytest.mark.django_db
def test_promo_discount_for_percent():
    race = _make_race(slug="promo-pct")
    promo = _promo(race, value=40)

    assert promo.discount_for(1000) == 400


@pytest.mark.django_db
def test_promo_discount_for_percent_rounds_down():
    race = _make_race(slug="promo-round")
    promo = _promo(race, value=33)

    # 1000 × 33 / 100 = 330.0 exactly; 999 × 33 / 100 = 329.67 → 329.
    assert promo.discount_for(999) == 329


@pytest.mark.django_db
def test_promo_discount_for_fixed():
    race = _make_race(slug="promo-fixed")
    promo = _promo(race, discount_type=RacePromo.FIXED, value=1000)

    assert promo.discount_for(2500) == 1000


@pytest.mark.django_db
def test_promo_discount_for_fixed_clamped_to_fee():
    race = _make_race(slug="promo-clamp")
    promo = _promo(race, discount_type=RacePromo.FIXED, value=5000)

    # A discount never exceeds the fee (the total must not go negative).
    assert promo.discount_for(1500) == 1500


@pytest.mark.django_db
def test_promo_discount_for_zero_fee():
    race = _make_race(slug="promo-zero")
    percent = _promo(race, code="P", value=50)
    fixed = _promo(race, code="F", discount_type=RacePromo.FIXED, value=500)

    assert percent.discount_for(0) == 0
    assert fixed.discount_for(0) == 0
    assert percent.discount_for(-100) == 0


@pytest.mark.django_db
def test_promo_discount_for_float_fee_returns_int():
    # Team.paid_people is a FloatField, so the fee can arrive as a float.
    race = _make_race(slug="promo-float")
    promo = _promo(race, value=40)

    result = promo.discount_for(1000.0)

    assert result == 400
    assert isinstance(result, int)


@pytest.mark.django_db
def test_promo_code_normalized_on_save():
    race = _make_race(slug="promo-norm")
    promo = RacePromo.objects.create(race=race, code="  sale40 ", value=40)

    promo.refresh_from_db()
    assert promo.code == "SALE40"


@pytest.mark.django_db
def test_promo_code_unique_within_race_only():
    from django.db import IntegrityError

    race = _make_race(slug="promo-uniq")
    other = _make_race(slug="promo-uniq-2")
    _promo(race, code="SALE")
    # Same code on another race is fine.
    _promo(other, code="SALE")

    with pytest.raises(IntegrityError):
        _promo(race, code="sale")


# --- Promo codes: resolve + quota ---

from django.utils import timezone  # noqa: E402

from apps.race.promo import PromoError, occupied_team_ids, resolve_promo  # noqa: E402
from website.models.race import RESERVATION_TTL  # noqa: E402


def _promo_payment(team, promo, status=Payment.STATUS_DONE, age=None):
    payment = Payment.objects.create(
        team=team, promo=promo, payment_amount=100, status=status
    )
    if age is not None:
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - age)
        payment.refresh_from_db()
    return payment


@pytest.mark.django_db
def test_resolve_promo_success_and_case_insensitive():
    _, race, team = _priced_team("pm1", slug="pm-ok")
    promo = _promo(race, code="SALE40")

    assert resolve_promo(race, " sale40 ", team) == promo


@pytest.mark.django_db
def test_resolve_promo_not_found():
    _, race, team = _priced_team("pm2", slug="pm-404")
    _promo(race, code="SALE40")

    for code in ("NOPE", "", None):
        with pytest.raises(PromoError) as exc:
            resolve_promo(race, code, team)
        assert exc.value.key == "not_found"


@pytest.mark.django_db
def test_resolve_promo_other_race_code_not_found():
    _, race, team = _priced_team("pm3", slug="pm-other")
    other = _make_race(slug="pm-other-2")
    _promo(other, code="SALE40")

    with pytest.raises(PromoError) as exc:
        resolve_promo(race, "SALE40", team)
    assert exc.value.key == "not_found"


@pytest.mark.django_db
def test_resolve_promo_inactive():
    _, race, team = _priced_team("pm4", slug="pm-off")
    _promo(race, code="SALE40", is_active=False)

    with pytest.raises(PromoError) as exc:
        resolve_promo(race, "SALE40", team)
    assert exc.value.key == "inactive"


@pytest.mark.django_db
def test_resolve_promo_already_used_by_this_team():
    _, race, team = _priced_team("pm5", slug="pm-used")
    promo = _promo(race, code="SALE40")
    _promo_payment(team, promo, status=Payment.STATUS_DONE)

    with pytest.raises(PromoError) as exc:
        resolve_promo(race, "SALE40", team)
    assert exc.value.key == "already_used"


@pytest.mark.django_db
def test_resolve_promo_limit_reached():
    owner, race, team = _priced_team("pm6", slug="pm-limit")
    promo = _promo(race, code="SALE40", max_uses=1)
    other_team = _make_team(owner, team.category2, start_number="2")
    _promo_payment(other_team, promo, status=Payment.STATUS_DONE)

    with pytest.raises(PromoError) as exc:
        resolve_promo(race, "SALE40", team)
    assert exc.value.key == "limit_reached"


@pytest.mark.django_db
def test_resolve_promo_unlimited_when_max_uses_zero():
    owner, race, team = _priced_team("pm7", slug="pm-unlim")
    promo = _promo(race, code="SALE40", max_uses=0)
    for i in range(3):
        other = _make_team(owner, team.category2, start_number=str(i + 2))
        _promo_payment(other, promo, status=Payment.STATUS_DONE)

    assert resolve_promo(race, "SALE40", team) == promo


@pytest.mark.django_db
def test_resolve_promo_own_live_draft_passes_and_holds_one_slot():
    _, race, team = _priced_team("pm8", slug="pm-draft")
    promo = _promo(race, code="SALE40", max_uses=1)
    # The team went to the bank: a live draft holds its own slot.
    _promo_payment(team, promo, status=Payment.STATUS_DRAFT)

    # A re-submit by the same team still resolves...
    assert resolve_promo(race, "SALE40", team) == promo
    # ...and a second draft does not multiply the quota.
    _promo_payment(team, promo, status=Payment.STATUS_DRAFT)
    assert occupied_team_ids(promo) == {team.id}


@pytest.mark.django_db
def test_resolve_promo_live_draft_of_other_team_blocks_last_slot():
    owner, race, team = _priced_team("pm9", slug="pm-draft-other")
    promo = _promo(race, code="SALE40", max_uses=1)
    other = _make_team(owner, team.category2, start_number="2")
    _promo_payment(other, promo, status=Payment.STATUS_DRAFT)

    with pytest.raises(PromoError) as exc:
        resolve_promo(race, "SALE40", team)
    assert exc.value.key == "limit_reached"


@pytest.mark.django_db
def test_expired_draft_frees_the_quota():
    owner, race, team = _priced_team("pm10", slug="pm-expired")
    promo = _promo(race, code="SALE40", max_uses=1)
    other = _make_team(owner, team.category2, start_number="2")
    _promo_payment(
        other,
        promo,
        status=Payment.STATUS_DRAFT,
        age=RESERVATION_TTL + datetime.timedelta(minutes=1),
    )

    assert occupied_team_ids(promo) == set()
    assert resolve_promo(race, "SALE40", team) == promo


@pytest.mark.django_db
def test_cancelled_payment_frees_the_quota_immediately():
    owner, race, team = _priced_team("pm11", slug="pm-cancel")
    promo = _promo(race, code="SALE40", max_uses=1)
    other = _make_team(owner, team.category2, start_number="2")
    _promo_payment(other, promo, status=Payment.STATUS_CANCEL)

    assert occupied_team_ids(promo) == set()
    assert resolve_promo(race, "SALE40", team) == promo


@pytest.mark.django_db
def test_draft_with_info_is_not_counted():
    owner, race, team = _priced_team("pm12", slug="pm-dwi")
    promo = _promo(race, code="SALE40", max_uses=1)
    other = _make_team(owner, team.category2, start_number="2")
    _promo_payment(other, promo, status=Payment.STATUS_DRAFT_WITH_INFO)

    assert occupied_team_ids(promo) == set()


@pytest.mark.django_db
def test_occupied_team_ids_ignores_teamless_payments():
    _, race, team = _priced_team("pm13", slug="pm-noteam")
    promo = _promo(race, code="SALE40", max_uses=1)
    Payment.objects.create(
        team=None, promo=promo, payment_amount=100, status=Payment.STATUS_DONE
    )

    assert occupied_team_ids(promo) == set()
    # An unsaved team (the add flow) must not look "already occupying".
    assert resolve_promo(race, "SALE40", Team()) == promo


@pytest.mark.django_db
def test_resolve_promo_unsaved_team_hits_the_global_limit():
    owner, race, team = _priced_team("pm14", slug="pm-unsaved")
    promo = _promo(race, code="SALE40", max_uses=1)
    other = _make_team(owner, team.category2, start_number="2")
    _promo_payment(other, promo, status=Payment.STATUS_DONE)

    with pytest.raises(PromoError) as exc:
        resolve_promo(race, "SALE40", Team())
    assert exc.value.key == "limit_reached"


# --- Promo codes: charge formula ---


@pytest.mark.django_db
def test_compute_team_charge_percent_promo():
    _, race, team = _priced_team("cp1", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="SALE40", value=40)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    # fee (3 − 1) × 1000 = 2000, −40% = 1200.
    assert discount == 800
    assert total == 1200
    assert lines == []


@pytest.mark.django_db
def test_compute_team_charge_fixed_promo():
    _, race, team = _priced_team("cp2", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="M1000", discount_type=RacePromo.FIXED, value=1000)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    assert discount == 1000
    assert total == 1000


@pytest.mark.django_db
def test_compute_team_charge_promo_larger_than_fee_floors_at_zero():
    _, race, team = _priced_team("cp3", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="FREE", discount_type=RacePromo.FIXED, value=9000)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    assert discount == 2000
    assert total == 0


@pytest.mark.django_db
def test_compute_team_charge_promo_does_not_discount_extras():
    _, race, team = _priced_team("cp4", cost=1000, ucount=3, paid_people=1)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=2, count_paid=0)
    promo = _promo(race, code="SALE50", value=50)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    # fee 2000 − 1000 = 1000, plus 2 × 500 at full price.
    assert discount == 1000
    assert total == 2000
    assert lines == [ExtraCharge(race_extra=transfer, count=2, unit_price=500)]


@pytest.mark.django_db
def test_compute_team_charge_promo_applies_to_unpaid_part_only():
    # A top-up: the discount is taken off what is actually being paid now.
    _, race, team = _priced_team("cp5", cost=1000, ucount=4, paid_people=2)
    promo = _promo(race, code="SALE50", value=50)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    assert discount == 1000
    assert total == 1000


@pytest.mark.django_db
def test_compute_team_charge_promo_on_fully_paid_team_is_noop():
    _, race, team = _priced_team("cp6", cost=1000, ucount=2, paid_people=2)
    promo = _promo(race, code="SALE50", value=50)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    assert discount == 0
    assert total == 0


@pytest.mark.django_db
def test_compute_team_charge_without_promo_unchanged():
    _, race, team = _priced_team("cp7", cost=1000, ucount=3, paid_people=1)
    _promo(race, code="SALE40", value=40)

    total, lines, discount = compute_team_charge(team, race)

    assert (total, discount) == (2000, 0)


@pytest.mark.django_db
def test_compute_team_charge_fractional_paid_people_stays_int():
    # Team.paid_people is a FloatField (member transfers can make it fractional).
    _, race, team = _priced_team("cp8", cost=1000, ucount=4, paid_people=1.5)
    promo = _promo(race, code="SALE33", value=33)

    total, lines, discount = compute_team_charge(team, race, promo=promo)

    # fee = int(2.5 × 1000) = 2500; 2500 × 33 // 100 = 825.
    assert isinstance(total, int)
    assert isinstance(discount, int)
    assert discount == 825
    assert total == 1675


# --- Promo codes: settlement service ---

from apps.race.settlement import settle_payment  # noqa: E402


@pytest.mark.django_db
def test_settle_payment_credits_people_and_extras():
    _, race, team = _priced_team("st1", cost=1000, ucount=4, paid_people=1)
    transfer = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500
    )
    TeamExtra.objects.create(team=team, race_extra=transfer, count=2, count_paid=0)
    payment = Payment.objects.create(
        team=team,
        payment_amount=4000,
        paid_for=3,
        status=Payment.STATUS_DRAFT,
    )
    PaymentExtra.objects.create(
        payment=payment, race_extra=transfer, count=2, unit_price=500
    )

    assert settle_payment(payment) is True

    team.refresh_from_db()
    payment.refresh_from_db()
    assert team.paid_people == 4
    assert team.paid_sum == 4000
    assert payment.status == Payment.STATUS_DONE
    assert payment.order == payment.pk
    assert team.extras.get(race_extra=transfer).count_paid == 2


@pytest.mark.django_db
def test_settle_payment_is_idempotent():
    _, race, team = _priced_team("st2", cost=1000, ucount=4, paid_people=1)
    payment = Payment.objects.create(
        team=team, payment_amount=3000, paid_for=3, status=Payment.STATUS_DRAFT
    )

    assert settle_payment(payment) is True
    assert settle_payment(payment) is False

    team.refresh_from_db()
    assert team.paid_people == 4


@pytest.mark.django_db
def test_settle_payment_flips_race_to_sold_out():
    _, race, team = _priced_team("st3", cost=1000, ucount=4, paid_people=1)
    race.people_limit = 4
    race.reg_status = RegStatus.OPEN
    race.save(update_fields=["people_limit", "reg_status"])
    payment = Payment.objects.create(
        team=team, payment_amount=3000, paid_for=3, status=Payment.STATUS_DRAFT
    )

    settle_payment(payment)

    race.refresh_from_db()
    assert race.reg_status == RegStatus.SOLD_OUT


# --- Promo codes: payment creation ---

from apps.race.promo import PromoUnavailable  # noqa: E402


def _patch_vtb():
    """Patch the VTB integration so payment creation does not hit the network."""
    from unittest.mock import patch

    return (
        patch("apps.race.pricing.VTBClient"),
        patch("apps.race.pricing.VTBPayment"),
        patch("apps.race.pricing.VTBPreparedPayment"),
    )


@pytest.mark.django_db
def test_create_team_payment_snapshots_promo(rf):
    owner, race, team = _priced_team("cpp1", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="SALE40", value=40)
    request = rf.post("/")
    request.user = owner

    from website.models import VTBPayment

    client_p, payment_p, prepared_p = _patch_vtb()
    with client_p as mock_client, payment_p as mock_payment, prepared_p as mock_prep:
        mock_payment.new_order_id.return_value = "ORDER_TEST"
        mock_payment.from_vtb_payload.return_value = VTBPayment.objects.create(
            order_id="ORDER_TEST", amount_value="1200.00", status="NEW"
        )
        mock_prep.objects.filter.return_value.first.return_value = None
        create_team_payment(request, team, race, promo=promo)
        order_kwargs = mock_client.return_value.create_order.call_args.kwargs

    payment = Payment.objects.get(team=team)
    assert payment.promo == promo
    assert payment.discount_amount == 800
    assert payment.payment_amount == 1200
    assert payment.payment_with_discount == 1200
    assert payment.paid_for == 2
    # The bank is asked for the discounted amount.
    assert order_kwargs["amount_value"] == 1200


@pytest.mark.django_db
def test_create_team_payment_raises_when_quota_gone(rf):
    owner, race, team = _priced_team("cpp2", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="SALE40", value=40, max_uses=1)
    request = rf.post("/")
    request.user = owner
    # Another team takes the last slot after the form validated.
    other = _make_team(owner, team.category2, start_number="2")
    _promo_payment(other, promo, status=Payment.STATUS_DONE)

    client_p, payment_p, prepared_p = _patch_vtb()
    with client_p, payment_p, prepared_p:
        with pytest.raises(PromoUnavailable):
            create_team_payment(request, team, race, promo=promo)

    assert not Payment.objects.filter(team=team).exists()


@pytest.mark.django_db
def test_create_team_payment_raises_when_promo_deactivated(rf):
    owner, race, team = _priced_team("cpp3", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="SALE40", value=40)
    request = rf.post("/")
    request.user = owner
    RacePromo.objects.filter(pk=promo.pk).update(is_active=False)

    client_p, payment_p, prepared_p = _patch_vtb()
    with client_p, payment_p, prepared_p:
        with pytest.raises(PromoUnavailable):
            create_team_payment(request, team, race, promo=promo)

    assert not Payment.objects.filter(team=team).exists()


@pytest.mark.django_db
def test_create_team_payment_full_discount_settles_without_vtb(rf):
    owner, race, team = _priced_team("cpp4", cost=1000, ucount=3, paid_people=1)
    promo = _promo(race, code="FREE", value=100)
    request = rf.post("/")
    request.user = owner

    client_p, payment_p, prepared_p = _patch_vtb()
    with client_p as mock_client, payment_p, prepared_p:
        result = create_team_payment(request, team, race, promo=promo)

    assert result is None
    mock_client.assert_not_called()
    payment = Payment.objects.get(team=team)
    assert payment.status == Payment.STATUS_DONE
    assert payment.payment_amount == 0
    assert payment.discount_amount == 2000
    assert payment.promo == promo
    team.refresh_from_db()
    assert team.paid_people == 3
    # The code's quota is occupied by this team now.
    assert occupied_team_ids(promo) == {team.id}


@pytest.mark.django_db
def test_create_team_payment_full_discount_credits_extras(rf):
    owner, race, team = _priced_team("cpp5", cost=1000, ucount=3, paid_people=1)
    free_extra = RaceExtra.objects.create(
        race=race, code="breakfast", name="Завтрак", price=0
    )
    TeamExtra.objects.create(team=team, race_extra=free_extra, count=2, count_paid=0)
    promo = _promo(race, code="FREE", value=100)
    request = rf.post("/")
    request.user = owner

    client_p, payment_p, prepared_p = _patch_vtb()
    with client_p, payment_p, prepared_p:
        result = create_team_payment(request, team, race, promo=promo)

    assert result is None
    assert team.extras.get(race_extra=free_extra).count_paid == 2


@pytest.mark.django_db
def test_create_team_payment_zero_without_discount_creates_nothing(rf):
    owner, race, team = _priced_team("cpp6", cost=1000, ucount=2, paid_people=2)
    promo = _promo(race, code="SALE40", value=40)
    request = rf.post("/")
    request.user = owner

    client_p, payment_p, prepared_p = _patch_vtb()
    with client_p, payment_p, prepared_p:
        result = create_team_payment(request, team, race, promo=promo)

    assert result is None
    assert not Payment.objects.filter(team=team).exists()
