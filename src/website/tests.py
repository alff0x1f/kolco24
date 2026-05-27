import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.urls import reverse

from website.models import Race
from website.models.models import Team
from website.models.race import Category, RegStatus


@pytest.mark.skip
@pytest.mark.django_db
def test_index_page(client):
    # Get the response for the index page
    url = reverse("index")  # Adjust the 'index' to match your URL pattern name
    response = client.get(url)

    # Check status code
    assert response.status_code == 200

    # Check the template used
    assert "website/index.html" in (t.name for t in response.templates)

    # Check the content of the index page
    assert "Кольцо24" in response.content.decode("utf-8")


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
    "agree_terms": True,
    "agree_privacy": True,
}


@pytest.mark.django_db
def test_reg_form_missing_agree_terms():
    from website.forms import RegForm

    data = {**REG_FORM_BASE, "agree_terms": False}
    form = RegForm(data=data)
    assert not form.is_valid()
    assert "agree_terms" in form.errors


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
def test_register_view_post_duplicate_email_shows_error(client):
    User.objects.create_user(
        username="existing", email="ivan@example.com", password="x"
    )
    response = client.post("/register/", REG_FORM_BASE)
    assert response.status_code == 200
    assert "reg_form" in response.context
    assert response.context["reg_form"].non_field_errors()


@pytest.mark.django_db
def test_register_view_post_missing_agreement_shows_error(client):
    data = {k: v for k, v in REG_FORM_BASE.items() if k != "agree_terms"}
    response = client.post("/register/", data)
    assert response.status_code == 200
    assert "reg_form" in response.context
    assert "agree_terms" in response.context["reg_form"].errors


@pytest.mark.django_db
def test_race_news_view_shows_form_for_admin(client):
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
