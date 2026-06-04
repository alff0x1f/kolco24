import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.race.models import PaymentExtra, RaceExtra, TeamExtra
from website.forms import TeamForm
from website.models import Payment, Race, VTBPayment
from website.models.models import Team
from website.models.race import (
    RESERVATION_TTL,
    Category,
    RaceLink,
    RacePriceTier,
    RegStatus,
)
from website.views.views_ import build_category_options, build_team_form_context


def test_new_order_id_shape_and_uniqueness():
    order_id = VTBPayment.new_order_id("ORDER")
    prefix, _, suffix = order_id.partition("_")
    assert prefix == "ORDER"
    assert len(suffix) == 26
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", suffix)
    assert VTBPayment.new_order_id("ORDER") != order_id
    assert VTBPayment.new_order_id("SPUTNIK").startswith("SPUTNIK_")


@pytest.mark.django_db
def test_logout_user_view(client):
    # Create a test user
    user = User.objects.create_user(
        username="testuser", password="password", email="testuser@example.com"
    )

    # Log in the user
    login_successful = client.login(
        username="testuser@example.com", password="password"
    )
    assert login_successful, "Login failed"

    # Ensure the user is authenticated
    assert client.session["_auth_user_id"] == str(user.pk)

    # Send a POST request to the logout view
    response = client.post(reverse("logout"), {"logout": "logout"})

    # Check that the response is a redirect
    assert response.status_code == 302
    assert response.url == "/"

    # Ensure the user is logged out
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_race_slug_populated():
    race = Race.objects.create(
        name="Test Race",
        code="test-race",
        slug="test-race",
    )
    assert race.slug == "test-race"


@pytest.mark.django_db
def test_race_slug_unique():
    Race.objects.create(name="Race A", code="race-a", slug="same-slug")
    with pytest.raises(IntegrityError):
        Race.objects.create(name="Race B", code="race-b", slug="same-slug")


@pytest.mark.django_db
def test_race_id_redirect_main(client):
    race = Race.objects.create(name="Test Race", code="tr2025", slug="tr2025")
    response = client.get(f"/race/{race.id}/")
    assert response.status_code == 301
    assert response["Location"] == f"/race/{race.slug}/"


@pytest.mark.django_db
def test_race_id_redirect_teams(client):
    race = Race.objects.create(name="Test Race", code="tr2025b", slug="tr2025b")
    response = client.get(f"/race/{race.id}/teams/")
    assert response.status_code == 301
    assert response["Location"] == f"/race/{race.slug}/teams/"


@pytest.mark.django_db
def test_legacy_teams_endpoint_returns_404(client):
    assert client.get("/teams/").status_code == 404


@pytest.mark.django_db
def test_race_slug_news_view(client):
    race = Race.objects.create(name="Test Race", code="tr2025c", slug="tr2025c")
    response = client.get(f"/race/{race.slug}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_edit_team_redirect_uses_slug(client):
    user = User.objects.create_user(
        username="edituser", password="pass", email="edit@example.com"
    )
    race = Race.objects.create(
        name="Edit Race",
        code="edit24",
        slug="edit-race-2024",
        is_teams_editable=True,
        reg_status=RegStatus.UPCOMING,
    )
    category = Category.objects.create(
        code="12h",
        name="12 hours",
        short_name="12h",
        race=race,
    )
    team = Team.objects.create(
        owner=user,
        paymentid="testpay",
        dist="12h",
        category2=category,
        ucount=2,
    )
    client.force_login(user)
    response = client.post(
        f"/team/{team.id}/",
        {"ucount": "2", "category2_id": str(category.id)},
    )
    assert response.status_code == 302
    assert race.slug in response["Location"]
    assert str(race.id) not in response["Location"]


@pytest.mark.django_db
def test_race_admin_model_creation():
    from django.contrib.auth.models import User

    from website.models.race import RaceAdmin

    race = Race.objects.create(name="Admin Race", code="ar2025", slug="admin-race-2025")
    user = User.objects.create_user(username="raceadmin", password="pass")
    ra = RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    assert str(ra) == f"{user} — {race} (admin)"
    assert ra.role == RaceAdmin.Role.ADMIN


@pytest.mark.django_db
def test_race_admin_unique_together():
    from django.contrib.auth.models import User

    from website.models.race import RaceAdmin

    race = Race.objects.create(
        name="Unique Race", code="ur2025", slug="unique-race-2025"
    )
    user = User.objects.create_user(username="uniqueadmin", password="pass")
    RaceAdmin.objects.create(race=race, user=user)
    with pytest.raises(IntegrityError):
        RaceAdmin.objects.create(race=race, user=user)


@pytest.mark.django_db
def test_newspost_xss_sanitization():
    from website.models.news import NewsPost

    race = Race.objects.create(name="XSS Race", code="xss1", slug="xss-race")
    post = NewsPost.objects.create(
        title="XSS Test",
        content='<script>alert("xss")</script> Normal **bold** text',
        race=race,
    )
    assert "<script>" not in post.content_html
    assert "alert" not in post.content_html
    assert "<strong>bold</strong>" in post.content_html


@pytest.mark.django_db
def test_newspost_xss_event_handler_stripped():
    from website.models.news import NewsPost

    race = Race.objects.create(name="XSS Race2", code="xss2", slug="xss-race-2")
    post = NewsPost.objects.create(
        title="Event Handler Test",
        content='<img src="x" onerror="alert(1)"> text',
        race=race,
    )
    assert "onerror" not in post.content_html


@pytest.mark.django_db
def test_news_post_form_valid():
    from website.forms import NewsPostForm

    form = NewsPostForm(
        data={"title": "Test Post", "content": "Some **markdown** text"}
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_news_post_form_invalid():
    from website.forms import NewsPostForm

    form = NewsPostForm(data={"title": "", "content": "Some content"})
    assert not form.is_valid()
    assert "title" in form.errors


@pytest.mark.django_db
def test_add_post_by_race_admin(client):
    from website.models.news import NewsPost
    from website.models.race import RaceAdmin

    race = Race.objects.create(name="Post Race", code="pr2025", slug="post-race-2025")
    user = User.objects.create_user(username="postadmin", password="pass")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    client.force_login(user)
    response = client.post(
        f"/race/{race.slug}/post/add/",
        {"title": "New Post", "content": "Hello world"},
    )
    assert response.status_code == 302
    assert NewsPost.objects.filter(race=race, title="New Post").exists()


@pytest.mark.django_db
def test_add_post_unauthorized(client):
    race = Race.objects.create(name="Post Race2", code="pr2026", slug="post-race-2026")
    response = client.post(
        f"/race/{race.slug}/post/add/",
        {"title": "Should fail", "content": "No auth"},
    )
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_add_post_non_admin_user(client):
    race = Race.objects.create(name="Post Race3", code="pr2027", slug="post-race-2027")
    user = User.objects.create_user(username="notadmin", password="pass")
    client.force_login(user)
    response = client.post(
        f"/race/{race.slug}/post/add/",
        {"title": "Should fail", "content": "Not admin"},
    )
    assert response.status_code == 403


REG_FORM_BASE = {
    "first_name": "Иван",
    "last_name": "Иванов",
    "email": "ivan@example.com",
    "phone": "+79001234567",
    "password": "secret123",
    "agree_privacy": True,
}


@pytest.mark.django_db
def test_reg_form_missing_agree_privacy():
    from website.forms import RegForm

    data = {**REG_FORM_BASE, "agree_privacy": False}
    form = RegForm(data=data)
    assert not form.is_valid()
    assert "agree_privacy" in form.errors


@pytest.mark.django_db
def test_reg_form_all_required_fields_valid():
    from website.forms import RegForm

    form = RegForm(data=REG_FORM_BASE)
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_reg_form_rejects_existing_email():
    from website.forms import RegForm

    User.objects.create_user(
        username="existing", email="ivan@example.com", password="x"
    )
    form = RegForm(data=REG_FORM_BASE)
    assert not form.is_valid()
    assert form.non_field_errors()


@pytest.mark.django_db
def test_reg_form_rejects_duplicate_email_case_insensitive():
    from website.forms import RegForm

    User.objects.create_user(
        username="existing", email="IVAN@EXAMPLE.COM", password="x"
    )
    form = RegForm(data=REG_FORM_BASE)  # submits "ivan@example.com"
    assert not form.is_valid()
    assert form.non_field_errors()


@pytest.mark.django_db
def test_register_view_post_creates_user(client):
    response = client.post("/accounts/register/", REG_FORM_BASE)
    assert response.status_code == 302
    assert User.objects.filter(email="ivan@example.com").exists()


@pytest.mark.django_db
def test_login_view_get(client):
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    assert "form" in response.context


@pytest.mark.django_db
def test_login_view_post_valid_credentials(client):
    User.objects.create_user(username="u", password="pass", email="u@example.com")
    response = client.post(
        "/accounts/login/", {"email": "u@example.com", "password": "pass"}
    )
    assert response.status_code == 302
    assert response.url == "/"
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_login_view_post_valid_credentials_next_redirect(client):
    # Simulate real browser flow: form action is /login/, ?next= comes from hidden field
    User.objects.create_user(username="u2", password="pass", email="u2@example.com")
    response = client.post(
        "/accounts/login/",
        {"email": "u2@example.com", "password": "pass", "next": "/race/kolco24_2025/"},
    )
    assert response.status_code == 302
    assert response.url == "/race/kolco24_2025/"


@pytest.mark.django_db
def test_login_view_post_invalid_credentials(client):
    User.objects.create_user(username="u3", password="pass", email="u3@example.com")
    response = client.post(
        "/accounts/login/", {"email": "u3@example.com", "password": "wrong"}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_passlogin_url_returns_404(client):
    response = client.get("/passlogin/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_transfer_urls_return_404(client):
    assert client.get("/race/8/transfer/").status_code == 404
    assert client.get("/race/8/transfer/list/").status_code == 404


@pytest.mark.django_db
def test_breakfast_urls_return_404(client):
    for path in [
        "/race/some-race/breakfast/",
        "/race/some-race/breakfast/admin/",
        "/race/some-race/breakfast/list/",
        "/race/1/breakfast/",
        "/race/1/breakfast/admin/",
        "/race/1/breakfast/list/",
    ]:
        assert client.get(path).status_code == 404, path


@pytest.mark.django_db
def test_login_view_post_blocks_open_redirect(client):
    User.objects.create_user(username="u4", password="pass", email="u4@example.com")
    response = client.post(
        "/accounts/login/?next=https://evil.com/steal",
        {"email": "u4@example.com", "password": "pass"},
    )
    assert response.status_code == 302
    assert response.url == "/"


@pytest.mark.django_db
def test_login_required_redirects_to_login_url(client):
    response = client.get("/page/x/edit/")
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.django_db
def test_register_view_post_duplicate_email_shows_error(client):
    User.objects.create_user(
        username="existing", email="ivan@example.com", password="x"
    )
    response = client.post("/accounts/register/", REG_FORM_BASE)
    assert response.status_code == 200
    assert "reg_form" in response.context
    assert response.context["reg_form"].non_field_errors()


@pytest.mark.django_db
def test_login_page_register_link_carries_next(client):
    response = client.get("/accounts/login/?next=/race/foo/teams/add/")
    assert response.status_code == 200
    html = response.content.decode()
    # The «Зарегистрироваться» link must forward next, or it's lost on register.
    assert "/accounts/register/?next=" in html


@pytest.mark.django_db
def test_register_view_get_passes_next_to_context(client):
    response = client.get("/accounts/register/?next=/race/foo/teams/add/")
    assert response.status_code == 200
    assert response.context["next"] == "/race/foo/teams/add/"


@pytest.mark.django_db
def test_register_view_post_honors_next_redirect(client):
    response = client.post(
        "/accounts/register/", {**REG_FORM_BASE, "next": "/race/foo/teams/add/"}
    )
    assert response.status_code == 302
    assert response.url == "/race/foo/teams/add/"
    assert User.objects.filter(email="ivan@example.com").exists()


@pytest.mark.django_db
def test_register_view_post_blocks_open_redirect(client):
    response = client.post(
        "/accounts/register/", {**REG_FORM_BASE, "next": "https://evil.com/steal"}
    )
    assert response.status_code == 302
    assert response.url == "/"


@pytest.mark.django_db
def test_race_page_view_shows_form_for_admin(client):
    from website.models.race import RaceAdmin

    race = Race.objects.create(name="Admin Race", code="ar2025", slug="admin-race-2025")
    admin_user = User.objects.create_user(username="newsadmin", password="pass")
    regular_user = User.objects.create_user(username="regularuser", password="pass")
    RaceAdmin.objects.create(race=race, user=admin_user, role=RaceAdmin.Role.ADMIN)

    client.force_login(admin_user)
    response = client.get(f"/race/{race.slug}/")
    assert response.status_code == 200
    assert "post_form" in response.context

    client.force_login(regular_user)
    response = client.get(f"/race/{race.slug}/")
    assert response.status_code == 200
    assert "post_form" not in response.context


@pytest.mark.django_db
def test_race_page_view_status_200(client):
    race = Race.objects.create(name="T", code="t25", slug="t-2025")
    response = client.get(f"/race/{race.slug}/")
    assert response.status_code == 200
    assert "race/race_page.html" in [t.name for t in response.templates]


@pytest.mark.django_db
def test_race_page_view_404_for_unknown_slug(client):
    response = client.get("/race/nonexistent-2099/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_race_page_view_context_keys(client):
    race = Race.objects.create(name="C", code="c25", slug="c-2025")
    response = client.get(f"/race/{race.slug}/")
    for key in (
        "categories",
        "links",
        "news_list",
        "news_count",
        "reg_open",
        "reg_upcoming",
        "race_team_count",
        "race_people_count",
    ):
        assert key in response.context, f"context missing: {key}"


@pytest.mark.django_db
def test_race_page_view_no_post_form_for_anon(client):
    race = Race.objects.create(name="A", code="a25", slug="a-2025")
    response = client.get(f"/race/{race.slug}/")
    assert "post_form" not in response.context


@pytest.mark.django_db
def test_race_page_view_reg_open_flag(client):
    race = Race.objects.create(
        name="R", code="ro1", slug="ro-2025", reg_status=RegStatus.OPEN
    )
    response = client.get(f"/race/{race.slug}/")
    assert response.context["reg_open"] is True
    assert response.context["reg_upcoming"] is False

    race.reg_status = RegStatus.UPCOMING
    race.save()
    response = client.get(f"/race/{race.slug}/")
    assert response.context["reg_open"] is False
    assert response.context["reg_upcoming"] is True

    race.reg_status = RegStatus.SOLD_OUT
    race.save()
    response = client.get(f"/race/{race.slug}/")
    assert response.context["reg_open"] is False
    assert response.context["reg_upcoming"] is False


@pytest.mark.django_db
def test_race_page_view_excludes_inactive_categories(client):
    race = Race.objects.create(name="R", code="rc2", slug="rc-2026")
    Category.objects.create(
        code="active", name="Active", short_name="A", race=race, is_active=True
    )
    Category.objects.create(
        code="inactive", name="Inactive", short_name="I", race=race, is_active=False
    )
    response = client.get(f"/race/{race.slug}/")
    codes = [c.code for c in response.context["categories"]]
    assert "active" in codes
    assert "inactive" not in codes


@pytest.mark.django_db
def test_race_page_view_news_list_capped_at_10(client):
    from website.models import NewsPost

    race = Race.objects.create(name="N", code="nl1", slug="nl-2025")
    for i in range(11):
        NewsPost.objects.create(race=race, title=f"Post {i}", content=f"body {i}")
    response = client.get(f"/race/{race.slug}/")
    # List is capped at 10, but the badge count reflects the true total (11).
    assert len(response.context["news_list"]) == 10
    assert response.context["news_count"] == 11


@pytest.mark.django_db
def test_add_post_invalid_form_shows_errors(client):
    from website.models.race import RaceAdmin

    race = Race.objects.create(name="R", code="ap1", slug="ap-2025")
    admin_user = User.objects.create_user(username="postadmin", password="pass")
    RaceAdmin.objects.create(race=race, user=admin_user, role=RaceAdmin.Role.ADMIN)
    client.force_login(admin_user)
    response = client.post(f"/race/{race.slug}/post/add/", {"title": "", "content": ""})
    assert response.status_code == 200
    assert "race/race_page.html" in [t.name for t in response.templates]
    assert "post_form" in response.context
    assert "title" in response.context["post_form"].errors


@pytest.mark.django_db
def test_race_clean_accepts_valid_url():
    race = Race.objects.create(
        name="URL Race",
        code="ur1",
        slug="url-race-1",
        place="Moscow",
        header_image="https://example.com/banner.jpg",
        header_logo="http://example.com/logo.png",
    )
    race.full_clean()


@pytest.mark.django_db
def test_race_clean_rejects_invalid_url():
    race = Race.objects.create(
        name="URL Race",
        code="ur2",
        slug="url-race-2",
        place="Moscow",
        header_image="not-a-url",
    )
    with pytest.raises(ValidationError) as exc_info:
        race.full_clean()
    assert "header_image" in exc_info.value.message_dict


@pytest.mark.django_db
def test_race_clean_accepts_blank_url():
    race = Race.objects.create(
        name="URL Race",
        code="ur3",
        slug="url-race-3",
        place="Moscow",
        header_image="",
        header_logo="",
    )
    race.full_clean()


@pytest.mark.django_db
def test_race_link_clean_accepts_valid_url():
    race = Race.objects.create(name="LR", code="lr1", slug="lr-2025")
    link = RaceLink.objects.create(race=race, name="Site", url="https://example.com/")
    link.full_clean()


@pytest.mark.django_db
def test_race_link_clean_rejects_invalid_url():
    race = Race.objects.create(name="LR", code="lr2", slug="lr-2026")
    link = RaceLink.objects.create(race=race, name="Bad", url="not-a-url")
    with pytest.raises(ValidationError) as exc_info:
        link.full_clean()
    assert "url" in exc_info.value.message_dict


@pytest.mark.django_db
def test_category_team_size_defaults():
    race = Race.objects.create(name="Cat Race", code="cat1", slug="cat-race-1")
    category = Category.objects.create(
        code="def",
        name="Defaults",
        short_name="def",
        race=race,
    )
    assert category.min_people == 2
    assert category.max_people == 6


def test_category_backfill_mapping():
    # Mirror the migration's mapping dict; verify representative ids resolve
    # to the same (min, max) tuples the legacy JS switch produced.
    import importlib

    migration = importlib.import_module(
        "website.migrations.0067_category_max_people_category_min_people"
    )
    mapping = migration.TEAM_SIZE_BACKFILL
    default = migration.DEFAULT_TEAM_SIZE

    # ids 8, 16 -> (2, 3)
    assert mapping[8] == (2, 3)
    assert mapping[16] == (2, 3)
    # ids 9, 13, 17, 21, 24 -> (4, 6)
    for cid in (9, 13, 17, 21, 24):
        assert mapping[cid] == (4, 6)
    # anything else -> (2, 2)
    assert mapping.get(999, default) == (2, 2)
    assert default == (2, 2)


@pytest.mark.django_db
def test_current_price_falls_back_to_cost_without_tiers():
    race = Race.objects.create(name="No Tiers", code="nt1", slug="no-tiers", cost=1500)
    assert race.current_price == 1500


@pytest.mark.django_db
def test_current_price_picks_active_tier():
    race = Race.objects.create(name="Tiers", code="t1", slug="tiers-1", cost=999)
    today = timezone.localdate()
    # earliest tier already past, second still active
    RacePriceTier.objects.create(
        race=race, price=1000, active_until=today - timedelta(days=10)
    )
    RacePriceTier.objects.create(
        race=race, price=1500, active_until=today + timedelta(days=10)
    )
    RacePriceTier.objects.create(
        race=race, price=2000, active_until=today + timedelta(days=20)
    )
    # earliest tier with active_until >= today is the 1500 one
    assert race.current_price == 1500


@pytest.mark.django_db
def test_current_price_uses_last_tier_when_all_past():
    race = Race.objects.create(name="Past", code="p1", slug="past-1", cost=999)
    today = timezone.localdate()
    RacePriceTier.objects.create(
        race=race, price=1000, active_until=today - timedelta(days=20)
    )
    RacePriceTier.objects.create(
        race=race, price=1500, active_until=today - timedelta(days=5)
    )
    # all past -> last tier (ordered by active_until) is charged
    assert race.current_price == 1500


@pytest.mark.django_db
def test_current_price_same_day_boundary_is_inclusive():
    race = Race.objects.create(name="Today", code="td1", slug="today-1", cost=999)
    today = timezone.localdate()
    RacePriceTier.objects.create(race=race, price=1200, active_until=today)
    RacePriceTier.objects.create(
        race=race, price=1800, active_until=today + timedelta(days=10)
    )
    # active_until == today must still count as active (inclusive >=)
    assert race.current_price == 1200


@pytest.mark.django_db
def test_price_tier_ladder_flags_statuses():
    race = Race.objects.create(name="Ladder", code="l1", slug="ladder-1", cost=999)
    today = timezone.localdate()
    past = RacePriceTier.objects.create(
        race=race, price=1000, active_until=today - timedelta(days=10)
    )
    active = RacePriceTier.objects.create(
        race=race, price=1500, active_until=today + timedelta(days=10)
    )
    future = RacePriceTier.objects.create(
        race=race, price=2000, active_until=today + timedelta(days=20)
    )
    ladder = race.price_tier_ladder()
    assert ladder == [
        {"tier": past, "status": "past"},
        {"tier": active, "status": "active"},
        {"tier": future, "status": "future"},
    ]


@pytest.mark.django_db
def test_price_tier_ladder_all_past_marks_last_active():
    race = Race.objects.create(name="LadderPast", code="lp1", slug="ladder-past-1")
    today = timezone.localdate()
    first = RacePriceTier.objects.create(
        race=race, price=1000, active_until=today - timedelta(days=20)
    )
    last = RacePriceTier.objects.create(
        race=race, price=1500, active_until=today - timedelta(days=5)
    )
    ladder = race.price_tier_ladder()
    assert ladder == [
        {"tier": first, "status": "past"},
        {"tier": last, "status": "active"},
    ]


@pytest.mark.django_db
def test_price_tier_ladder_empty_without_tiers():
    race = Race.objects.create(name="Empty", code="e1", slug="empty-1", cost=500)
    assert race.price_tier_ladder() == []


# --- Task 3: unified view context, current_price charging, server guards ---


def _create_team_for_edit(
    *,
    suffix,
    reg_status=RegStatus.OPEN,
    is_teams_editable=True,
    cost=1000,
    tier_price=None,
    min_people=4,
    max_people=6,
    ucount=4,
    paid_people=4,
    map_count=0,
    map_count_paid=0,
):
    user = User.objects.create_user(
        username=f"owner-{suffix}",
        password="pass",
        email=f"owner-{suffix}@example.com",
    )
    race = Race.objects.create(
        name="R",
        code=f"rc{suffix}",
        slug=f"slug-{suffix}",
        cost=cost,
        reg_status=reg_status,
        is_teams_editable=is_teams_editable,
    )
    if tier_price is not None:
        RacePriceTier.objects.create(
            race=race,
            price=tier_price,
            active_until=timezone.localdate() + timedelta(days=10),
        )
    category = Category.objects.create(
        code="t",
        name="Team",
        short_name="T",
        race=race,
        min_people=min_people,
        max_people=max_people,
    )
    team = Team.objects.create(
        owner=user,
        paymentid="p",
        dist="t",
        category2=category,
        ucount=ucount,
        paid_people=paid_people,
        map_count=map_count,
        map_count_paid=map_count_paid,
    )
    return user, race, category, team


def _mock_vtb():
    """Patch the VTB integration so a charging POST does not hit the network.

    Payment creation now lives in ``apps.race.pricing.create_team_payment``,
    so the VTB names are patched there (not in the views).
    """
    client_patch = patch("apps.race.pricing.VTBClient")
    payment_patch = patch("apps.race.pricing.VTBPayment")
    prepared_patch = patch("apps.race.pricing.VTBPreparedPayment")
    return client_patch, payment_patch, prepared_patch


@pytest.mark.django_db
def test_get_add_team_context_has_price_and_counts(client):
    user = User.objects.create_user(
        username="adder", password="pass", email="adder@example.com"
    )
    race = Race.objects.create(
        name="Add Race",
        code="addr1",
        slug="add-race-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
    )
    RacePriceTier.objects.create(
        race=race, price=1500, active_until=timezone.localdate() + timedelta(days=10)
    )
    category = Category.objects.create(
        code="t",
        name="Team",
        short_name="T",
        race=race,
        min_people=4,
        max_people=6,
    )
    RaceExtra.objects.create(
        race=race, code="map", name="Доп. карты", price=200, free_per_team=2
    )
    client.force_login(user)
    response = client.get(reverse("add_team", args=[race.slug]))
    assert response.status_code == 200
    # current_price comes from the active tier, not race.cost
    assert response.context["current_price"] == 1500
    assert response.context["price_tiers"]  # non-empty ladder
    # add-ons surface as the generic `extras` config (maps is just code="map")
    extras = response.context["extras"]
    assert len(extras) == 1
    assert extras[0]["code"] == "map"
    assert extras[0]["price"] == 200
    assert extras[0]["freePerTeam"] == 2
    assert extras[0]["count"] == 0
    assert extras[0]["countPaid"] == 0
    options = response.context["category_options"]
    match = next(o for o in options if o["id"] == category.id)
    assert match["counts"] == [4, 5, 6]


@pytest.mark.django_db
def test_get_edit_team_renders_edit_template(client):
    user, race, category, team = _create_team_for_edit(suffix="rt", tier_price=1500)
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    assert response.status_code == 200
    assert "website/edit_team.html" in [t.name for t in response.templates]
    assert response.context["current_price"] == 1500


@pytest.mark.django_db
def test_edit_team_charges_delta_on_ucount_growth(client):
    user, race, category, team = _create_team_for_edit(
        suffix="grow", tier_price=1500, ucount=4, paid_people=4
    )
    client.force_login(user)
    client_p, payment_p, prepared_p = _mock_vtb()
    with client_p, payment_p as mock_payment, prepared_p as mock_prepared:
        oid = VTBPayment.new_order_id("ORDER")
        mock_payment.new_order_id.return_value = oid
        mock_payment.from_vtb_payload.return_value = _make_vtb_payment(
            oid, pay_url="https://pay.example/redirect"
        )
        mock_prepared.objects.filter.return_value.first.return_value = None
        response = client.post(
            reverse("edit_team", args=[team.id]),
            {"ucount": "5", "category2_id": str(category.id), "map_count": "0"},
        )
    assert response.status_code == 302
    payment = Payment.objects.filter(team=team).order_by("-id").first()
    # delta = (5 - 4) * 1500 (the tier price, NOT race.cost=1000)
    assert payment.payment_amount == 1500
    assert payment.cost_per_person == 1500
    assert payment.vtb_payment is not None


@pytest.mark.django_db
def test_edit_team_charges_delta_on_maps_added(client):
    user, race, category, team = _create_team_for_edit(
        suffix="maps", tier_price=1500, ucount=4, paid_people=4
    )
    extra = RaceExtra.objects.create(
        race=race, code="map", name="Доп. карты", price=200, free_per_team=2
    )
    client.force_login(user)
    client_p, payment_p, prepared_p = _mock_vtb()
    with client_p, payment_p as mock_payment, prepared_p as mock_prepared:
        oid = VTBPayment.new_order_id("ORDER")
        mock_payment.new_order_id.return_value = oid
        mock_payment.from_vtb_payload.return_value = _make_vtb_payment(
            oid, pay_url="https://pay.example/redirect"
        )
        mock_prepared.objects.filter.return_value.first.return_value = None
        response = client.post(
            reverse("edit_team", args=[team.id]),
            {"ucount": "4", "category2_id": str(category.id), "extra_map": "2"},
        )
    assert response.status_code == 302
    payment = Payment.objects.filter(team=team).order_by("-id").first()
    # only the 2 extra maps are charged: 2 * 200
    assert payment.payment_amount == 400
    assert payment.vtb_payment is not None
    pe = PaymentExtra.objects.get(payment=payment, race_extra=extra)
    assert pe.count == 2
    assert pe.unit_price == 200
    te = TeamExtra.objects.get(team=team, race_extra=extra)
    assert te.count == 2
    assert te.count_paid == 0  # only credited at reconciliation


@pytest.mark.django_db
def test_edit_team_no_charge_when_nothing_added(client):
    user, race, category, team = _create_team_for_edit(
        suffix="noop", tier_price=1500, ucount=4, paid_people=4
    )
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"ucount": "4", "category2_id": str(category.id), "map_count": "0"},
    )
    assert response.status_code == 302
    assert Payment.objects.filter(team=team).count() == 0


@pytest.mark.django_db
def test_edit_team_rejects_out_of_range_ucount(client):
    user, race, category, team = _create_team_for_edit(
        suffix="oor", min_people=4, max_people=6, ucount=4, paid_people=0
    )
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"ucount": "2", "category2_id": str(category.id), "map_count": "0"},
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.ucount == 4  # unchanged — invalid POST rejected
    assert Payment.objects.filter(team=team).count() == 0


@pytest.mark.django_db
def test_edit_team_rejects_over_cap_map_count(client):
    user, race, category, team = _create_team_for_edit(
        suffix="cap", min_people=4, max_people=6, ucount=4, paid_people=0
    )
    RaceExtra.objects.create(
        race=race, code="map", name="Доп. карты", price=200, free_per_team=2
    )
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        # ucount=4 allows max(0, 4-2)=2 maps; 5 must be rejected
        {"ucount": "4", "category2_id": str(category.id), "extra_map": "5"},
    )
    assert response.status_code == 200
    assert Payment.objects.filter(team=team).count() == 0


@pytest.mark.django_db
def test_edit_team_closed_but_editable_saves_without_charge(client):
    user, race, category, team = _create_team_for_edit(
        suffix="closed",
        reg_status=RegStatus.SOLD_OUT,
        is_teams_editable=True,
        ucount=4,
        paid_people=0,
    )
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"ucount": "5", "category2_id": str(category.id), "map_count": "0"},
    )
    # editable gate passes, but reg not open -> saved, no payment
    assert response.status_code == 302
    assert Payment.objects.filter(team=team).count() == 0
    team.refresh_from_db()
    assert team.ucount == 5


@pytest.mark.django_db
def test_edit_team_open_but_not_editable_forbidden(client):
    user, race, category, team = _create_team_for_edit(
        suffix="ne",
        reg_status=RegStatus.OPEN,
        is_teams_editable=False,
        ucount=4,
        paid_people=0,
    )
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"ucount": "5", "category2_id": str(category.id), "map_count": "0"},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_add_team_rejects_out_of_range_ucount(client):
    user = User.objects.create_user(
        username="addoor", password="pass", email="addoor@example.com"
    )
    race = Race.objects.create(
        name="Add OOR",
        code="addoor1",
        slug="add-oor-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    category = Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=4, max_people=6
    )
    client.force_login(user)
    response = client.post(
        reverse("add_team", args=[race.slug]),
        {"ucount": "2", "category2_id": str(category.id), "map_count": "0"},
    )
    assert response.status_code == 200
    assert not Team.objects.filter(category2=category).exists()


def _echo_order_payload(order_id, order_name, amount_value, return_payment_data):
    """create_order stub that echoes the minted order_id back in the payload."""
    return {
        "object": {
            "orderId": order_id,
            "orderCode": f"CODE_{order_id}",
            "amount": {"value": str(amount_value), "code": "RUB"},
            "status": {
                "value": "CREATED",
                "description": "CREATED",
                "changedAt": "2026-03-08T12:00:00Z",
            },
            "createdAt": "2026-03-08T11:59:00Z",
            "expire": "2026-03-09T11:59:00Z",
            "payUrl": "https://pay.example/sbp",
            "preparedPayments": [],
        }
    }


@pytest.mark.django_db
@patch("apps.race.pricing.VTBClient._ensure_token", return_value=None)
@patch("apps.race.pricing.VTBClient.create_order", side_effect=_echo_order_payload)
def test_add_team_mints_ulid_order_id_and_links_fk(
    create_order_mock, _token_mock, client
):
    user = User.objects.create_user(
        username="ulidadd", password="pass", email="ulidadd@example.com"
    )
    race = Race.objects.create(
        name="ULID Add",
        code="ulidadd1",
        slug="ulid-add-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    category = Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    client.force_login(user)
    response = client.post(
        reverse("add_team", args=[race.slug]),
        {"ucount": "2", "category2_id": str(category.id), "map_count": "0"},
    )

    assert response.status_code == 302
    # create_order was called with a ULID order id (ORDER_<ulid>), not ORDER_<int>.
    order_id = create_order_mock.call_args.kwargs["order_id"]
    assert order_id.startswith("ORDER_")
    assert not order_id.split("_", 1)[1].isdigit()

    payment = Payment.objects.get(team__category2=category)
    assert payment.vtb_payment is not None
    assert payment.vtb_payment.order_id == order_id


# --- Reconciliation: _resolve_race_payment (FK + legacy fallback) ---


def _make_vtb_payment(order_id, pay_url=""):
    return VTBPayment.objects.create(
        order_id=order_id,
        amount_value="1000.00",
        status="PAID",
        pay_url=pay_url,
    )


@pytest.mark.django_db
def test_resolve_race_payment_via_fk():
    from website.management.commands.check_vtb_payments import Command

    vtb_payment = _make_vtb_payment(VTBPayment.new_order_id("ORDER"))
    payment = Payment.objects.create(
        payment_method="sbp2",
        payment_amount=1000,
        paid_for=2,
        status=Payment.STATUS_DRAFT,
        vtb_payment=vtb_payment,
    )

    resolved = Command()._resolve_race_payment(vtb_payment)
    assert resolved == payment


@pytest.mark.django_db
def test_resolve_race_payment_legacy_fallback():
    from website.management.commands.check_vtb_payments import Command

    payment = Payment.objects.create(
        payment_method="sbp2",
        payment_amount=1000,
        paid_for=2,
        status=Payment.STATUS_DRAFT,
    )
    vtb_payment = _make_vtb_payment(f"ORDER_{payment.id}")

    resolved = Command()._resolve_race_payment(vtb_payment)
    assert resolved == payment


@pytest.mark.django_db
def test_resolve_race_payment_unparseable_returns_none():
    from website.management.commands.check_vtb_payments import Command

    vtb_payment = _make_vtb_payment("ORDER_notanumber")

    assert Command()._resolve_race_payment(vtb_payment) is None


@pytest.mark.django_db
def test_resolve_race_payment_legacy_fallback_no_row_returns_none():
    from website.management.commands.check_vtb_payments import Command

    # Integer suffix that parses, but no Payment row with that pk exists.
    vtb_payment = _make_vtb_payment("ORDER_999999999")

    assert Command()._resolve_race_payment(vtb_payment) is None


# --- Task 5: add_team.html rewritten on base-2 ---


@pytest.mark.django_db
def test_add_team_renders_base2_template(client):
    user = User.objects.create_user(
        username="b2add", password="pass", email="b2add@example.com"
    )
    race = Race.objects.create(
        name="Base2 Race",
        code="b2r1",
        slug="base2-race-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    RacePriceTier.objects.create(
        race=race, price=1500, active_until=timezone.localdate() + timedelta(days=10)
    )
    Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=4, max_people=6
    )
    client.force_login(user)
    response = client.get(reverse("add_team", args=[race.slug]))
    assert response.status_code == 200
    assert "website/add_team.html" in [t.name for t in response.templates]

    html = response.content.decode()
    # base-2 design: shared assets are linked
    assert "/static/css/team-form.css" in html
    assert "/static/js/team-form.js" in html
    # segmented count control + category data-counts (from min/max people)
    assert 'id="ucountSeg"' in html
    assert 'data-counts="4,5,6"' in html
    # consent input gates submit in add mode
    assert 'id="consent"' in html
    assert 'name="consent"' in html
    # JSON config island consumed by team-form.js
    assert 'id="teamFormConfig"' in html
    assert '"isEdit": false' in html
    # scoped wrapper so team-form.css never leaks
    assert "team-register" in html


@pytest.mark.django_db
def test_add_team_config_island_uses_current_price(client):
    user = User.objects.create_user(
        username="b2price", password="pass", email="b2price@example.com"
    )
    race = Race.objects.create(
        name="Base2 Price",
        code="b2p1",
        slug="base2-price-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    RacePriceTier.objects.create(
        race=race, price=1500, active_until=timezone.localdate() + timedelta(days=10)
    )
    Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    client.force_login(user)
    response = client.get(reverse("add_team", args=[race.slug]))
    html = response.content.decode()
    # config island carries the active-tier price, not race.cost
    assert '"currentPrice": 1500' in html


@pytest.mark.django_db
def test_add_team_hides_submit_when_not_editable(client):
    user = User.objects.create_user(
        username="b2closed", password="pass", email="b2closed@example.com"
    )
    race = Race.objects.create(
        name="Base2 Closed",
        code="b2c1",
        slug="base2-closed-1",
        cost=1000,
        reg_status=RegStatus.SOLD_OUT,
        is_teams_editable=False,
    )
    Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    client.force_login(user)
    response = client.get(reverse("add_team", args=[race.slug]))
    assert response.status_code == 200
    html = response.content.decode()
    # submit gate follows the server flag (is_teams_editable)
    assert 'id="submitBtn"' not in html
    assert 'id="payBtn"' not in html
    # status tag is display-only and reflects reg_status
    assert "is-closed" in html


# --- Task 6: edit_team.html on base-2 (edit flow + edit-only sections) ---


@pytest.mark.django_db
def test_edit_team_renders_base2_template(client):
    user, race, category, team = _create_team_for_edit(
        suffix="b2edit", tier_price=1500, ucount=4, paid_people=0
    )
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    assert response.status_code == 200
    assert "website/edit_team.html" in [t.name for t in response.templates]

    html = response.content.decode()
    # base-2 design: shared assets are linked
    assert "/static/css/team-form.css" in html
    assert "/static/js/team-form.js" in html
    # segmented count control + scoped wrapper
    assert 'id="ucountSeg"' in html
    assert "team-register" in html
    # JSON config island marks edit mode
    assert 'id="teamFormConfig"' in html
    assert '"isEdit": true' in html
    # consent is pre-checked and disabled in edit mode
    assert 'name="consent" checked disabled' in html
    # submit reads "Сохранить" with a доплата label swap available to the JS
    assert 'data-label-due="Сохранить и&nbsp;доплатить"' in html


@pytest.mark.django_db
def test_edit_team_shows_move_section_when_paid_and_editable(client):
    user, race, category, team = _create_team_for_edit(
        suffix="mvshow", ucount=4, paid_people=4, is_teams_editable=True
    )
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    html = response.content.decode()
    # member-transfer section + its dedicated POST target
    assert "Переносы участников" in html
    assert reverse("move_team_member", args=[team.id]) in html
    assert 'name="moved_people"' in html


@pytest.mark.django_db
def test_edit_team_hides_move_section_when_not_paid(client):
    user, race, category, team = _create_team_for_edit(
        suffix="mvhide", ucount=4, paid_people=0, is_teams_editable=True
    )
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    html = response.content.decode()
    assert "Переносы участников" not in html


@pytest.mark.django_db
def test_edit_team_shows_delete_section_when_deletable(client):
    user, race, category, team = _create_team_for_edit(
        suffix="del", ucount=4, paid_people=0
    )
    assert team.can_be_deleted
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    html = response.content.decode()
    assert "Удалить команду" in html
    assert 'name="delete_team"' in html


@pytest.mark.django_db
def test_edit_team_hides_delete_section_when_not_deletable(client):
    user, race, category, team = _create_team_for_edit(
        suffix="nodel", ucount=4, paid_people=4
    )
    assert not team.can_be_deleted
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    html = response.content.decode()
    assert 'name="delete_team"' not in html


@pytest.mark.django_db
def test_edit_team_shows_payment_history(client):
    user, race, category, team = _create_team_for_edit(
        suffix="hist", ucount=4, paid_people=4
    )
    Payment.objects.create(
        owner=user,
        team=team,
        payment_method="sbp2",
        payment_amount=6000,
        paid_for=4,
        status=Payment.STATUS_DONE,
        sender_card_number="",
    )
    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    html = response.content.decode()
    assert "История оплат" in html
    assert "6000" in html


@pytest.mark.django_db
def test_add_team_omits_edit_only_sections(client):
    user = User.objects.create_user(
        username="addnoedit", password="pass", email="addnoedit@example.com"
    )
    race = Race.objects.create(
        name="Add No Edit",
        code="ane1",
        slug="add-no-edit-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=4, max_people=6
    )
    client.force_login(user)
    response = client.get(reverse("add_team", args=[race.slug]))
    html = response.content.decode()
    # the add page is for new teams — no edit-only sections
    assert "История оплат" not in html
    assert "Переносы участников" not in html
    assert "Удалить команду" not in html
    assert 'name="delete_team"' not in html
    assert '"isEdit": false' in html


@pytest.mark.django_db
def test_edit_team_rejects_ucount_above_max(client):
    user, race, category, team = _create_team_for_edit(
        suffix="abvmax", min_people=4, max_people=6, ucount=4, paid_people=0
    )
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"ucount": "7", "category2_id": str(category.id), "map_count": "0"},
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.ucount == 4  # unchanged — invalid POST rejected
    assert Payment.objects.filter(team=team).count() == 0


@pytest.mark.django_db
def test_edit_team_redirects_unauthenticated(client):
    user, race, category, team = _create_team_for_edit(suffix="unauth")
    response = client.get(reverse("edit_team", args=[team.id]))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_delete_team_not_found_for_non_owner(client):
    user, race, category, team = _create_team_for_edit(
        suffix="deloth", ucount=4, paid_people=0
    )
    other = User.objects.create_user(
        username="other-deloth", password="pass", email="other-deloth@example.com"
    )
    client.force_login(other)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"delete_team": "1"},
    )
    # get_team filters by owner, so non-owner sees 404
    assert response.status_code == 404
    assert Team.objects.filter(pk=team.pk).exists()  # team untouched


@pytest.mark.django_db
def test_delete_team_rejected_when_paid(client):
    user, race, category, team = _create_team_for_edit(
        suffix="delpaid", ucount=4, paid_people=4
    )
    assert not team.can_be_deleted
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"delete_team": "1"},
    )
    assert response.status_code == 400
    team.refresh_from_db()
    assert not team.is_deleted


@pytest.mark.django_db
def test_delete_team_soft_deletes_and_redirects(client):
    user, race, category, team = _create_team_for_edit(
        suffix="delok", ucount=4, paid_people=0
    )
    assert team.can_be_deleted
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"delete_team": "1"},
    )
    assert response.status_code == 302
    assert reverse("my_teams", args=[race.slug]) in response["Location"]
    # TeamManager filters is_deleted=False, so a deleted team disappears from objects
    assert not Team.objects.filter(pk=team.pk).exists()


@pytest.mark.django_db
def test_build_category_options_single_size(client):
    user = User.objects.create_user(
        username="single", password="pass", email="single@example.com"
    )
    race = Race.objects.create(
        name="Single",
        code="sng1",
        slug="single-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    category = Category.objects.create(
        code="s", name="Solo", short_name="S", race=race, min_people=2, max_people=2
    )
    client.force_login(user)
    response = client.get(reverse("add_team", args=[race.slug]))
    assert response.status_code == 200
    options = response.context["category_options"]
    match = next(o for o in options if o["id"] == category.id)
    assert match["counts"] == [2]


# ---------------------------------------------------------------------------
# Task 1: people-limit fields & occupancy helpers
# ---------------------------------------------------------------------------


def _pl_user(django_user_model, n=0):
    return django_user_model.objects.create_user(username=f"pl_owner_{n}", password="x")


def _pl_race(**kwargs):
    kwargs.setdefault("name", "PL Race")
    kwargs.setdefault("code", "pl-race")
    kwargs.setdefault("slug", "pl-race")
    return Race.objects.create(**kwargs)


def _pl_category(race, **kwargs):
    kwargs.setdefault("code", "M")
    kwargs.setdefault("name", "Men")
    kwargs.setdefault("short_name", "M")
    return Category.objects.create(race=race, **kwargs)


def _pl_team(category, owner, paid_people=0, is_deleted=False, name="T", ucount=None):
    return Team.objects.create(
        owner=owner,
        teamname=name,
        paymentid=f"pay-{name}",
        dist=category.code,
        category2=category,
        ucount=ucount if ucount is not None else max(paid_people, 1),
        paid_people=paid_people,
        is_deleted=is_deleted,
    )


@pytest.mark.django_db
def test_category_people_count_sums_paid_people(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=2, name="A")
    _pl_team(cat, user, paid_people=3, name="B")
    _pl_team(cat, user, paid_people=0, name="Draft")
    assert cat.people_count() == 5


@pytest.mark.django_db
def test_category_people_count_excludes_deleted(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=2, name="A")
    _pl_team(cat, user, paid_people=4, is_deleted=True, name="Gone")
    assert cat.people_count() == 2


@pytest.mark.django_db
def test_race_people_count_excludes_deleted(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=2, name="A")
    _pl_team(cat, user, paid_people=4, is_deleted=True, name="Gone")
    assert race.people_count() == 2


@pytest.mark.django_db
def test_category_remaining_people_unlimited_when_zero(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race, people_limit=0)
    _pl_team(cat, user, paid_people=2)
    assert cat.remaining_people() is None


@pytest.mark.django_db
def test_category_remaining_people_with_limit(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race, people_limit=6)
    _pl_team(cat, user, paid_people=2)
    assert cat.remaining_people() == 4


@pytest.mark.django_db
def test_category_remaining_people_excludes_self_team(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race, people_limit=6)
    team = _pl_team(cat, user, paid_people=2, name="self")
    _pl_team(cat, user, paid_people=3, name="Other")
    # without exclusion: 6 - 5 = 1
    assert cat.remaining_people() == 1
    # excluding the team itself frees its 2 slots: 6 - 3 = 3
    assert cat.remaining_people(exclude_team=team) == 3
    # excluding a team from a different category has no effect
    other_cat = _pl_category(race, code="W", name="Women", short_name="W")
    foreign = _pl_team(other_cat, user, paid_people=2, name="foreign")
    assert cat.remaining_people(exclude_team=foreign) == 1


@pytest.mark.django_db
def test_race_remaining_people_unlimited_when_zero(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=2)
    assert race.remaining_people() is None


@pytest.mark.django_db
def test_race_remaining_people_with_limit(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=2, name="A")
    _pl_team(cat, user, paid_people=3, name="B")
    assert race.remaining_people() == 5


# ---------------------------------------------------------------------------
# Task 2: capacity gate in TeamForm
# ---------------------------------------------------------------------------


def _gate_data(category, ucount, map_count=0):
    return {
        "ucount": str(ucount),
        "category2_id": str(category.id),
        "map_count": str(map_count),
        "dist": category.code,
    }


@pytest.mark.django_db
def test_gate_race_full_blocks_growth(django_user_model):
    # Гонка полная (лимит достигнут); рост состава 2→3 блокируется.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=5)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=5, name="filler")
    team = _pl_team(cat, user, paid_people=2, name="self")
    form = TeamForm(race.id, _gate_data(cat, 3), team=team)
    assert not form.is_valid()
    assert "ucount" in form.errors


@pytest.mark.django_db
def test_gate_pure_transfer_allowed_when_race_full(django_user_model):
    # Гонка полная, но в целевой категории есть места: чистый переход 2→2 ок.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4)
    src = _pl_category(race, code="A", name="A", short_name="A", people_limit=0)
    dst = _pl_category(race, code="B", name="B", short_name="B", people_limit=10)
    _pl_team(dst, user, paid_people=2, name="filler")  # race now at 4 (full)
    team = _pl_team(src, user, paid_people=2, name="self")
    form = TeamForm(race.id, _gate_data(dst, 2), team=team)
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_gate_category_full_blocks_entry(django_user_model):
    # Перейти в полную категорию нельзя.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0)
    src = _pl_category(race, code="A", name="A", short_name="A")
    dst = _pl_category(race, code="B", name="B", short_name="B", people_limit=2)
    _pl_team(dst, user, paid_people=2, name="filler")  # category full
    team = _pl_team(src, user, paid_people=2, name="self")
    form = TeamForm(race.id, _gate_data(dst, 2), team=team)
    assert not form.is_valid()
    assert "category2_id" in form.errors


@pytest.mark.django_db
def test_gate_edit_self_no_growth_in_full_category_allowed(django_user_model):
    # Категория полная за счёт самой команды: edit без роста разрешён.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0)
    cat = _pl_category(race, people_limit=4)
    _pl_team(cat, user, paid_people=2, name="other")
    team = _pl_team(cat, user, paid_people=2, name="self")  # category at 4 (full)
    form = TeamForm(race.id, _gate_data(cat, 2), team=team, current_category_id=cat.id)
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_gate_growth_in_full_category_blocked(django_user_model):
    # Рост состава в полной категории блокируется.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0)
    cat = _pl_category(race, people_limit=4)
    _pl_team(cat, user, paid_people=2, name="other")
    team = _pl_team(cat, user, paid_people=2, name="self")
    form = TeamForm(race.id, _gate_data(cat, 3), team=team, current_category_id=cat.id)
    assert not form.is_valid()
    assert "category2_id" in form.errors


@pytest.mark.django_db
def test_gate_new_registration_into_full_race_blocked(django_user_model):
    # Новая регистрация (team=Team()) в полную гонку блокируется.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=4, name="filler")
    form = TeamForm(race.id, _gate_data(cat, 2))  # team defaults to Team()
    assert not form.is_valid()
    assert "ucount" in form.errors


@pytest.mark.django_db
def test_gate_draft_teams_do_not_occupy_slots(django_user_model):
    # paid_people=0 черновики не занимают слот → регистрация проходит.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=0, name="draft1")
    _pl_team(cat, user, paid_people=0, name="draft2")
    form = TeamForm(race.id, _gate_data(cat, 3))
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_gate_bypass_limits_skips_gate(django_user_model):
    # bypass_limits (суперюзер) пропускает gate несмотря на полную гонку.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=2)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=2, name="filler")
    form = TeamForm(race.id, _gate_data(cat, 4), bypass_limits=True)
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_gate_unlimited_does_not_restrict(django_user_model):
    # people_limit=0 (гонка и категория) не ограничивает.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0)
    cat = _pl_category(race, people_limit=0)
    _pl_team(cat, user, paid_people=6, name="filler")
    form = TeamForm(race.id, _gate_data(cat, 6))
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_gate_exactly_fills_last_slot(django_user_model):
    # needed == race_remaining → разрешено (условие > а не >=).
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=5)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=3, name="filler")
    team = _pl_team(cat, user, paid_people=0, name="self")
    form = TeamForm(race.id, _gate_data(cat, 2), team=team)
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_gate_moving_and_growing_in_full_category_blocked(django_user_model):
    # Переход в другую категорию с одновременным ростом блокируется категорийным гейтом.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0)
    src = _pl_category(race, code="A", name="A", short_name="A")
    dst = _pl_category(race, code="B", name="B", short_name="B", people_limit=4)
    _pl_team(dst, user, paid_people=3, name="filler")
    team = _pl_team(src, user, paid_people=2, name="self")
    form = TeamForm(race.id, _gate_data(dst, 5), team=team)
    assert not form.is_valid()
    assert "category2_id" in form.errors


# ---------------------------------------------------------------------------
# Seat reservation: живые draft-платежи держат места занятыми (RESERVATION_TTL)
# ---------------------------------------------------------------------------


def _draft_payment(team, *, age_minutes=0, status=Payment.STATUS_DRAFT):
    """Создать Payment для команды и при необходимости состарить ``created_at``.

    ``created_at`` — ``auto_now_add``, поэтому возраст задаётся отдельным
    ``update`` (в обход auto_now_add).
    """
    payment = Payment.objects.create(
        owner=team.owner,
        team=team,
        payment_method="sbp2",
        payment_amount=0,
        paid_for=max(0, int(team.ucount) - team.paid_people),
        status=status,
    )
    if age_minutes:
        Payment.objects.filter(pk=payment.pk).update(
            created_at=timezone.now() - timedelta(minutes=age_minutes)
        )
    return payment


@pytest.mark.django_db
def test_reserved_people_counts_live_draft(django_user_model):
    # Живой draft резервирует max(0, ucount − paid_people) мест.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    team = _pl_team(cat, user, paid_people=0, ucount=3, name="A")
    _draft_payment(team)
    assert race.reserved_people() == 3
    assert cat.reserved_people() == 3


@pytest.mark.django_db
def test_reserved_people_partial_paid_team(django_user_model):
    # Доплата: бронируется только остаток до полного состава (ucount − paid).
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    team = _pl_team(cat, user, paid_people=2, ucount=5, name="A")
    _draft_payment(team)
    assert race.reserved_people() == 3


@pytest.mark.django_db
def test_reserved_people_ignores_stale_draft(django_user_model):
    # Черновик старше RESERVATION_TTL место не держит.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    team = _pl_team(cat, user, paid_people=0, ucount=3, name="A")
    stale = int(RESERVATION_TTL.total_seconds() // 60) + 1
    _draft_payment(team, age_minutes=stale)
    assert race.reserved_people() == 0


@pytest.mark.django_db
def test_reserved_people_ignores_non_draft(django_user_model):
    # Оплаченный (done) платёж не считается бронью.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    team = _pl_team(cat, user, paid_people=0, ucount=3, name="A")
    _draft_payment(team, status=Payment.STATUS_DONE)
    assert race.reserved_people() == 0


@pytest.mark.django_db
def test_reserved_people_counts_team_once(django_user_model):
    # Несколько живых черновиков одной команды → бронь не множится (distinct).
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    team = _pl_team(cat, user, paid_people=0, ucount=3, name="A")
    _draft_payment(team)
    _draft_payment(team)
    assert race.reserved_people() == 3


@pytest.mark.django_db
def test_reserved_people_self_exclusion(django_user_model):
    # exclude_team снимает собственную бронь команды.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    team = _pl_team(cat, user, paid_people=0, ucount=3, name="A")
    _draft_payment(team)
    assert race.reserved_people() == 3
    assert race.reserved_people(exclude_team=team) == 0
    assert cat.reserved_people(exclude_team=team) == 0


@pytest.mark.django_db
def test_remaining_people_subtracts_reservations(django_user_model):
    # Остаток = лимит − оплаченные − забронированные.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race, people_limit=10)
    _pl_team(cat, user, paid_people=4, name="paid")  # оплачено 4
    reserver = _pl_team(cat, user, paid_people=0, ucount=3, name="reserver")
    _draft_payment(reserver)  # бронь 3
    assert race.remaining_people() == 3  # 10 − 4 − 3
    assert cat.remaining_people() == 3


@pytest.mark.django_db
def test_gate_blocks_when_reservation_fills_race(django_user_model):
    # Суть фикса: бронь первой команды занимает места → вторая упирается в лимит.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4)
    cat = _pl_category(race, people_limit=0)
    reserver = _pl_team(cat, user, paid_people=0, ucount=3, name="reserver")
    _draft_payment(reserver)  # бронь 3 из 4
    # Новая регистрация на 2 человека: нужно 2, свободно 1 → блок.
    form = TeamForm(race.id, _gate_data(cat, 2))
    assert not form.is_valid()
    assert "ucount" in form.errors


@pytest.mark.django_db
def test_gate_own_reservation_does_not_block_edit(django_user_model):
    # Владелец редактирует свою команду с собственным живым черновиком — не блок.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4)
    cat = _pl_category(race, people_limit=0)
    team = _pl_team(cat, user, paid_people=0, ucount=4, name="self")
    _draft_payment(team)  # бронь 4 — гонка «полна» собственной бронью
    form = TeamForm(race.id, _gate_data(cat, 4), team=team, current_category_id=cat.id)
    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# Task 3: авто sold_out при подтверждении оплаты
# ---------------------------------------------------------------------------


def _confirm_payment(user, team, paid_for, cost_per_person=1500):
    """Подтвердить оплату, повторяя check_vtb_payments._settle_race_payment."""
    with transaction.atomic():
        team.refresh_from_db()
        amount = paid_for * cost_per_person
        payment = Payment.objects.create(
            owner=user,
            team=team,
            payment_method="sbp2",
            status=Payment.STATUS_DONE,
            payment_amount=amount,
            payment_with_discount=amount,
            cost_per_person=cost_per_person,
            paid_for=paid_for,
            sender_card_number="",
        )
        payment.order = payment.pk
        payment.save(update_fields=["order"])
        team.paid_people += paid_for
        team.paid_sum += amount
        team.save(update_fields=["paid_people", "paid_sum"])

        category = team.category2
        race = category.race if category else None
        if (
            race
            and race.people_limit
            and race.reg_status == RegStatus.OPEN
            and race.people_count() >= race.people_limit
        ):
            race.reg_status = RegStatus.SOLD_OUT
            race.save(update_fields=["reg_status"])


@pytest.mark.django_db
def test_payment_reaching_cap_flips_sold_out(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4, reg_status=RegStatus.OPEN)
    cat = _pl_category(race)
    team = _pl_team(cat, user, paid_people=2, name="self")
    # occupancy 2 → оплата на ещё 2 → 4 >= limit(4) → sold_out
    _confirm_payment(user, team, paid_for=2)
    race.refresh_from_db()
    assert race.reg_status == RegStatus.SOLD_OUT


@pytest.mark.django_db
def test_deleting_team_does_not_reopen(django_user_model):
    # Option B: после авто sold_out удаление команды не реоткрывает регистрацию.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=4, reg_status=RegStatus.OPEN)
    cat = _pl_category(race)
    team = _pl_team(cat, user, paid_people=2, name="self")
    _confirm_payment(user, team, paid_for=2)  # 2 + 2 = 4 → sold_out
    race.refresh_from_db()
    assert race.reg_status == RegStatus.SOLD_OUT
    # освобождаем места — авто-реоткрытия нет
    team.refresh_from_db()
    team.is_deleted = True
    team.save()
    race.refresh_from_db()
    assert race.reg_status == RegStatus.SOLD_OUT


@pytest.mark.django_db
def test_payment_below_cap_does_not_flip(django_user_model):
    # OPEN гонка не переходит в SOLD_OUT, пока occupancy < limit.
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10, reg_status=RegStatus.OPEN)
    cat = _pl_category(race)
    team = _pl_team(cat, user, paid_people=2, name="self")
    _confirm_payment(user, team, paid_for=2)  # occupancy 4 < 10
    race.refresh_from_db()
    assert race.reg_status == RegStatus.OPEN


@pytest.mark.django_db
def test_manual_sold_out_below_cap_untouched(django_user_model):
    # Ручной sold_out ниже cap не сбрасывается (флип только OPEN → SOLD_OUT).
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10, reg_status=RegStatus.SOLD_OUT)
    cat = _pl_category(race)
    team = _pl_team(cat, user, paid_people=2, name="self")
    _confirm_payment(user, team, paid_for=2)  # occupancy 4 < 10
    race.refresh_from_db()
    assert race.reg_status == RegStatus.SOLD_OUT


@pytest.mark.django_db
def test_race_without_limit_does_not_flip(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=0, reg_status=RegStatus.OPEN)
    cat = _pl_category(race)
    team = _pl_team(cat, user, paid_people=2, name="self")
    _confirm_payment(user, team, paid_for=10)
    race.refresh_from_db()
    assert race.reg_status == RegStatus.OPEN


# ---------------------------------------------------------------------------
# Task 5: team-form UX context (data-remaining / raceRemaining)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_category_options_remaining_limited_and_unlimited(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    limited = _pl_category(race, code="L", name="Lim", short_name="L", people_limit=6)
    unlimited = _pl_category(race, code="U", name="Unl", short_name="U", people_limit=0)
    _pl_team(limited, user, paid_people=2, name="A")
    options = build_category_options(race.id)
    by_id = {o["id"]: o for o in options}
    # limited: 6 - 2 = 4 free; unlimited published as "" (no limit)
    assert by_id[limited.id]["remaining"] == 4
    assert by_id[unlimited.id]["remaining"] == ""


@pytest.mark.django_db
def test_build_category_options_excludes_own_team(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race, people_limit=6)
    team = _pl_team(cat, user, paid_people=2, name="self")
    _pl_team(cat, user, paid_people=3, name="Other")
    # without exclusion 6 - 5 = 1; excluding own team frees its 2 → 3
    plain = build_category_options(race.id)
    assert next(o for o in plain if o["id"] == cat.id)["remaining"] == 1
    excluded = build_category_options(race.id, cat.id, team=team)
    assert next(o for o in excluded if o["id"] == cat.id)["remaining"] == 3


@pytest.mark.django_db
def test_build_category_options_marks_current(django_user_model):
    user = _pl_user(django_user_model)
    race = _pl_race()
    cat = _pl_category(race, code="C", name="Cur", short_name="C", people_limit=2)
    other = _pl_category(race, code="O", name="Oth", short_name="O", people_limit=2)
    team = _pl_team(cat, user, paid_people=2, name="self")
    options = build_category_options(race.id, cat.id, team=team)
    by_id = {o["id"]: o for o in options}
    # the team's own (full) category is flagged so the client never disables it
    assert by_id[cat.id]["is_current"] is True
    assert by_id[other.id]["is_current"] is False


@pytest.mark.django_db
def test_build_team_form_context_includes_race_remaining(django_user_model):
    import json

    user = _pl_user(django_user_model)
    race = _pl_race(people_limit=10)
    cat = _pl_category(race)
    _pl_team(cat, user, paid_people=4, name="A")
    ctx = build_team_form_context(race, Team())
    config = json.loads(str(ctx["team_form_config_json"]))
    assert config["raceRemaining"] == 6
    assert config["currentCategoryId"] is None


@pytest.mark.django_db
def test_build_team_form_context_race_remaining_unlimited(django_user_model):
    import json

    race = _pl_race(people_limit=0)
    ctx = build_team_form_context(race, Team())
    config = json.loads(str(ctx["team_form_config_json"]))
    assert config["raceRemaining"] is None


@pytest.mark.django_db
def test_edit_team_invalid_post_superuser_has_bypass_limits_in_config(
    client, django_user_model
):
    import json

    superuser = django_user_model.objects.create_superuser(
        username="su_bypass_test", password="x"
    )
    race = _pl_race(people_limit=2)
    cat = _pl_category(race)
    filler_user = _pl_user(django_user_model, n=77)
    _pl_team(cat, filler_user, paid_people=2, name="filler77")
    team = _pl_team(cat, superuser, paid_people=0, name="SuTeam")
    client.force_login(superuser)
    # ucount=1 is below min_value=2, so form.is_valid() is False → invalid-POST rerender
    response = client.post(
        f"/team/{team.id}/",
        {"ucount": "1", "category2_id": str(cat.id)},
    )
    assert response.status_code == 200
    config = json.loads(str(response.context["team_form_config_json"]))
    assert config["bypassLimits"] is True


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "команд"),
        (1, "команда"),
        (2, "команды"),
        (4, "команды"),
        (5, "команд"),
        (11, "команд"),  # 11–14 are the «many» exception
        (12, "команд"),
        (14, "команд"),
        (21, "команда"),
        (22, "команды"),
        (25, "команд"),
        (111, "команд"),
        (2.0, "команды"),  # FloatField counts coerce via float→int
        (None, "команд"),  # bad input falls back to «many»
    ],
)
def test_ru_plural_picks_correct_form(n, expected):
    from website.templatetags.custom_filters import ru_plural

    assert ru_plural(n, "команда,команды,команд") == expected


# ---------------------------------------------------------------------------
# Task 4: generic add-ons cutover (extras replace the hardcoded maps feature)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_add_team_with_transfer_creates_payment_and_extra(client):
    user = User.objects.create_user(
        username="trf", password="pass", email="trf@example.com"
    )
    race = Race.objects.create(
        name="Trf Race",
        code="trf1",
        slug="trf-race-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    category = Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, free_per_team=0
    )
    client.force_login(user)
    client_p, payment_p, prepared_p = _mock_vtb()
    with client_p, payment_p as mock_payment, prepared_p as mock_prepared:
        oid = VTBPayment.new_order_id("ORDER")
        mock_payment.new_order_id.return_value = oid
        mock_payment.from_vtb_payload.return_value = _make_vtb_payment(
            oid, pay_url="https://pay.example/redirect"
        )
        mock_prepared.objects.filter.return_value.first.return_value = None
        response = client.post(
            reverse("add_team", args=[race.slug]),
            {"ucount": "2", "category2_id": str(category.id), "extra_transfer": "2"},
        )
    assert response.status_code == 302
    team = Team.objects.get(category2=category)
    payment = Payment.objects.get(team=team)
    # fee (2 × 1000) + transfer (2 × 500)
    assert payment.payment_amount == 3000
    pe = PaymentExtra.objects.get(payment=payment, race_extra=extra)
    assert pe.count == 2
    assert pe.unit_price == 500
    te = TeamExtra.objects.get(team=team, race_extra=extra)
    assert te.count == 2
    assert te.count_paid == 0


@pytest.mark.django_db
def test_edit_team_charges_only_extra_delta(client):
    user, race, category, team = _create_team_for_edit(
        suffix="delta", tier_price=1500, ucount=4, paid_people=4
    )
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=300, free_per_team=0
    )
    TeamExtra.objects.create(team=team, race_extra=extra, count=1, count_paid=1)
    client.force_login(user)
    client_p, payment_p, prepared_p = _mock_vtb()
    with client_p, payment_p as mock_payment, prepared_p as mock_prepared:
        oid = VTBPayment.new_order_id("ORDER")
        mock_payment.new_order_id.return_value = oid
        mock_payment.from_vtb_payload.return_value = _make_vtb_payment(
            oid, pay_url="https://pay.example/redirect"
        )
        mock_prepared.objects.filter.return_value.first.return_value = None
        response = client.post(
            reverse("edit_team", args=[team.id]),
            {"ucount": "4", "category2_id": str(category.id), "extra_transfer": "3"},
        )
    assert response.status_code == 302
    payment = Payment.objects.filter(team=team).order_by("-id").first()
    # only the 2-unit delta (3 desired − 1 paid) at 300 each
    assert payment.payment_amount == 600
    pe = PaymentExtra.objects.get(payment=payment, race_extra=extra)
    assert pe.count == 2
    assert pe.unit_price == 300


@pytest.mark.django_db
def test_add_team_rejects_over_cap_extra(client):
    user = User.objects.create_user(
        username="ovc", password="pass", email="ovc@example.com"
    )
    race = Race.objects.create(
        name="OverCap",
        code="ovc1",
        slug="overcap-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        is_teams_editable=True,
    )
    category = Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    RaceExtra.objects.create(
        race=race, code="map", name="Доп. карты", price=200, free_per_team=2
    )
    client.force_login(user)
    response = client.post(
        reverse("add_team", args=[race.slug]),
        # ucount=4 allows max(0, 4-2)=2 maps; 10 must be rejected
        {"ucount": "4", "category2_id": str(category.id), "extra_map": "10"},
    )
    assert response.status_code == 200
    assert not Team.objects.filter(category2=category).exists()


@pytest.mark.django_db
def test_edit_team_rejects_extra_drop_below_paid(client):
    user, race, category, team = _create_team_for_edit(
        suffix="dropbelow", tier_price=1500, ucount=4, paid_people=4
    )
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=300, free_per_team=0
    )
    TeamExtra.objects.create(team=team, race_extra=extra, count=2, count_paid=2)
    client.force_login(user)
    response = client.post(
        reverse("edit_team", args=[team.id]),
        {"ucount": "4", "category2_id": str(category.id), "extra_transfer": "1"},
    )
    assert response.status_code == 200
    assert Payment.objects.filter(team=team).count() == 0


@pytest.mark.django_db
def test_superuser_bypass_capacity_but_extra_cap_still_applies(django_user_model):
    # bypass_limits skips the capacity gate but per-extra caps still bind.
    su = django_user_model.objects.create_superuser(
        username="su-cap", email="su-cap@example.com", password="pass"
    )
    race = Race.objects.create(
        name="SUcap",
        code="sucap1",
        slug="sucap-1",
        cost=1000,
        reg_status=RegStatus.OPEN,
        people_limit=2,
    )
    cat = Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    Team.objects.create(
        owner=su, paymentid="f1", dist="t", category2=cat, ucount=2, paid_people=2
    )
    RaceExtra.objects.create(
        race=race, code="map", name="Доп. карты", price=200, free_per_team=2
    )
    data = {
        "ucount": "4",
        "category2_id": str(cat.id),
        "extra_map": "10",
        "dist": cat.code,
    }
    form = TeamForm(race.id, data, bypass_limits=True)
    assert not form.is_valid()
    assert "extra_map" in form.errors  # cap still enforced
    assert "ucount" not in form.errors  # capacity bypassed


def test_team_form_defensive_init_with_querydict():
    # TeamForm must tolerate a non-int/None race_id without raising.
    from django.http import QueryDict

    qd = QueryDict("ucount=2&teamname=x")
    form = TeamForm(qd)  # race_id is a QueryDict (a non-int value)
    assert form.extras == []
    assert not any(name.startswith("extra_") for name in form.fields)


@pytest.mark.django_db
def test_reconciliation_credits_extras_once_idempotent():
    from website.management.commands.check_vtb_payments import Command

    user = User.objects.create_user(
        username="rec", password="pass", email="rec@example.com"
    )
    race = Race.objects.create(
        name="Rec", code="rec1", slug="rec-1", cost=1000, reg_status=RegStatus.OPEN
    )
    cat = Category.objects.create(
        code="t", name="Team", short_name="T", race=race, min_people=2, max_people=6
    )
    team = Team.objects.create(
        owner=user, paymentid="r1", dist="t", category2=cat, ucount=4, paid_people=2
    )
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=300, free_per_team=0
    )
    payment = Payment.objects.create(
        owner=user,
        team=team,
        payment_method="sbp2",
        payment_amount=2600,
        paid_for=2,
        status=Payment.STATUS_DRAFT,
    )
    PaymentExtra.objects.create(
        payment=payment, race_extra=extra, count=2, unit_price=300
    )

    cmd = Command()
    # first pass settles (no TeamExtra exists yet → get_or_create)
    assert cmd._settle_race_payment(payment) is True
    te = TeamExtra.objects.get(team=team, race_extra=extra)
    assert te.count_paid == 2
    assert te.count == 2  # clamped up from default 0
    team.refresh_from_db()
    assert team.paid_people == 4

    # second pass short-circuits on status == done → no double credit
    assert cmd._settle_race_payment(payment) is False
    te.refresh_from_db()
    assert te.count_paid == 2
    team.refresh_from_db()
    assert team.paid_people == 4


@pytest.mark.django_db
def test_config_island_mirrors_compute_team_charge(client):
    import json

    from apps.race.pricing import compute_team_charge

    user, race, category, team = _create_team_for_edit(
        suffix="mirror", tier_price=1500, ucount=4, paid_people=2
    )
    extra = RaceExtra.objects.create(
        race=race, code="transfer", name="Трансфер", price=500, free_per_team=0
    )
    TeamExtra.objects.create(team=team, race_extra=extra, count=3, count_paid=1)

    client.force_login(user)
    response = client.get(reverse("edit_team", args=[team.id]))
    config = json.loads(str(response.context["team_form_config_json"]))
    # the island carries exactly the fields the JS live-total needs
    assert config["currentPrice"] == 1500
    assert config["paidPeople"] == 2
    assert len(config["extras"]) == 1
    e = config["extras"][0]
    assert {"code", "name", "price", "freePerTeam", "count", "countPaid"} <= set(e)
    assert e["price"] == 500
    assert e["count"] == 3
    assert e["countPaid"] == 1

    # server formula: (4−2)×1500 + (3−1)×500 = 3000 + 1000 = 4000
    total, lines = compute_team_charge(team, race)
    assert total == 4000
    assert len(lines) == 1
    assert lines[0].count == 2
    assert lines[0].unit_price == 500


@pytest.mark.django_db
def test_custom_404_page_renders(client):
    resp = client.get("/no-such-page/")
    assert resp.status_code == 404
    assert "сбились с маршрута" in resp.content.decode()
    assert "404.html" in [t.name for t in resp.templates]


@pytest.mark.django_db
def test_custom_403_page_renders():
    # 403 extends base-2.html, whose footer calls {% footer_menu %} (a DB query),
    # so the render legitimately touches the DB — hence @pytest.mark.django_db.
    from django.template.loader import get_template

    html = get_template("403.html").render({})
    assert "Доступ закрыт" in html
    assert "is-403" in html


@pytest.mark.django_db
def test_custom_403_page_http_response(rf):
    from django.contrib.auth.models import AnonymousUser
    from django.core.exceptions import PermissionDenied
    from django.views.defaults import permission_denied

    request = rf.get("/")
    request.user = AnonymousUser()
    resp = permission_denied(request, PermissionDenied())
    assert resp.status_code == 403
    assert "Доступ закрыт" in resp.content.decode()
    assert "is-403" in resp.content.decode()


def test_custom_500_page_renders_standalone():
    # 500 is standalone: rendered by server_error with an empty Context() and no
    # context processors (the DB may be the cause of the 500). It must NOT extend
    # base-2.html and must touch no DB — hence NO @pytest.mark.django_db here;
    # that absence is itself part of what this test guarantees.
    from django.template.loader import get_template

    html = get_template("500.html").render({})
    assert "Что-то сломалось на дистанции" in html
    assert "<!doctype html>" in html.lower()
