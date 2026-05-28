import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse

from website.models import Race
from website.models.models import Team
from website.models.race import Category, RaceLink, RegStatus


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
    assert response.status_code == 403


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
    response = client.post("/register/", REG_FORM_BASE)
    assert response.status_code == 302
    assert User.objects.filter(email="ivan@example.com").exists()


@pytest.mark.django_db
def test_login_view_get(client):
    response = client.get("/login/")
    assert response.status_code == 200
    assert "form" in response.context


@pytest.mark.django_db
def test_login_view_post_valid_credentials(client):
    User.objects.create_user(username="u", password="pass", email="u@example.com")
    response = client.post("/login/", {"email": "u@example.com", "password": "pass"})
    assert response.status_code == 302
    assert response.url == "/"
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_login_view_post_valid_credentials_next_redirect(client):
    # Simulate real browser flow: form action is /login/, ?next= comes from hidden field
    User.objects.create_user(username="u2", password="pass", email="u2@example.com")
    response = client.post(
        "/login/",
        {"email": "u2@example.com", "password": "pass", "next": "/race/kolco24_2025/"},
    )
    assert response.status_code == 302
    assert response.url == "/race/kolco24_2025/"


@pytest.mark.django_db
def test_login_view_post_invalid_credentials(client):
    User.objects.create_user(username="u3", password="pass", email="u3@example.com")
    response = client.post("/login/", {"email": "u3@example.com", "password": "wrong"})
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_passlogin_url_returns_404(client):
    response = client.get("/passlogin/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_login_view_post_blocks_open_redirect(client):
    User.objects.create_user(username="u4", password="pass", email="u4@example.com")
    response = client.post(
        "/login/?next=https://evil.com/steal",
        {"email": "u4@example.com", "password": "pass"},
    )
    assert response.status_code == 302
    assert response.url == "/"


@pytest.mark.django_db
def test_login_required_redirects_to_login_url(client):
    response = client.get("/team/")
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_register_view_post_duplicate_email_shows_error(client):
    User.objects.create_user(
        username="existing", email="ivan@example.com", password="x"
    )
    response = client.post("/register/", REG_FORM_BASE)
    assert response.status_code == 200
    assert "reg_form" in response.context
    assert response.context["reg_form"].non_field_errors()


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
