import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.urls import reverse

from website.models import Race


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
