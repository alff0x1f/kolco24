import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from website.models import Race, Tag, Team, TeamMemberRaceLog, TeamStartLog
from website.models.race import Category


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
def test_member_race_log_view_shows_entries(client):
    admin = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="secret123"
    )
    assert client.login(username="admin", password="secret123")

    race = Race.objects.create(name="Test Race", code="test-race")
    category = Category.objects.create(
        code="cat",
        short_name="CAT",
        name="Category",
        race=race,
    )
    owner = User.objects.create_user(username="captain", password="pass12345")
    team = Team.objects.create(
        owner=owner,
        paymentid="pid",
        dist="dist",
        ucount=1,
        teamname="Test Team",
        category2=category,
        start_number="42",
    )

    tag = Tag.objects.create(number=101, tag_id="TAG001")
    TeamStartLog.objects.create(
        race=race,
        team=team,
        member_tags=[tag.tag_id],
        start_timestamp=1_700_000_000_000,
    )

    TeamMemberRaceLog.objects.create(
        race=race,
        member_tag=tag,
        start_time=1_700_000_000_000,
        finish_time=1_700_000_500_000,
    )

    url = reverse("race_member_logs", args=[race.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "TAG001" in content
    assert "Test Team" in content
