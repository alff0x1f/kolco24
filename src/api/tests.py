import datetime
from decimal import Decimal

import pytest
from django.test import override_settings
from rest_framework.test import APITestCase

from donate.models import ClubMember, DonationPeriod, MemberDonation
from website.models import Tag


class MemberTagAPITestCase(APITestCase):
    def test_create_member_tag(self):
        url = "/api/member_tag/"
        data = {"number": 1, "nfc_uid": "123ab"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["number"], 1)
        self.assertEqual(response.data["nfc_uid"], "123AB")
        self.assertIsNone(response.data["last_seen_at"])

    def test_list_member_tags(self):
        url = "/api/member_tag/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_touch_member_tag_updates_last_seen(self):
        tag = Tag.objects.create(number=1, nfc_uid="123ab")

        url = "/api/member_tag/touch/"
        response = self.client.post(url, {"nfc_uid": tag.nfc_uid}, format="json")

        self.assertEqual(response.status_code, 200)
        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_seen_at)
        self.assertEqual(response.data["id"], tag.id)
        self.assertEqual(response.data["nfc_uid"], tag.nfc_uid)
        self.assertIsNotNone(response.data["last_seen_at"])

    def test_touch_member_tag_leaves_updated_at_unchanged(self):
        # A scan (touch) bumps last_seen_at but must NOT move updated_at, so it
        # stays invisible to the mobile member-tags fingerprint. See the carve-out
        # comment in api/views/tag.py:MemberTagTouchView.
        tag = Tag.objects.create(number=1, nfc_uid="123ab")
        original_updated_at = tag.updated_at
        original_last_seen_at = tag.last_seen_at

        url = "/api/member_tag/touch/"
        response = self.client.post(url, {"nfc_uid": tag.nfc_uid}, format="json")

        self.assertEqual(response.status_code, 200)
        tag.refresh_from_db()
        self.assertEqual(tag.updated_at, original_updated_at)
        self.assertNotEqual(tag.last_seen_at, original_last_seen_at)
        self.assertIsNotNone(tag.last_seen_at)

    def test_touch_member_tag_lowercase_uid_finds_tag(self):
        Tag.objects.create(number=1, nfc_uid="123AB")

        url = "/api/member_tag/touch/"
        response = self.client.post(url, {"nfc_uid": "123ab"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nfc_uid"], "123AB")

    def test_touch_member_tag_not_found_returns_404(self):
        url = "/api/member_tag/touch/"
        response = self.client.post(url, {"nfc_uid": "NONEXISTENT"}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertIn("nfc_uid", response.data)

    def test_create_member_tag_duplicate_uid_returns_400(self):
        Tag.objects.create(number=1, nfc_uid="ABC123")
        url = "/api/member_tag/"
        response = self.client.post(
            url, {"number": 2, "nfc_uid": "abc123"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nfc_uid", response.data)


URL = "/api/contributors/"
TOKEN = "test-secret-token"


@override_settings(CONTRIBUTORS_API_TOKEN=TOKEN)
class ContributorsAPITestCase(APITestCase):
    def setUp(self):
        self.period = DonationPeriod.objects.create(
            name="весна 2024",
            date=datetime.date(2024, 3, 1),
            is_active=True,
        )
        self.member = ClubMember.objects.create(
            name="Алексей Костров", notes="Горная школа"
        )
        self.donation = MemberDonation.objects.create(
            member=self.member,
            period=self.period,
            is_paid=True,
            amount=Decimal("1500.00"),
            paid_date=datetime.date(2024, 4, 10),
            recipient=MemberDonation.RECIPIENT_SBP,
            note="",
        )

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}

    def test_returns_data_with_valid_token(self):
        response = self.client.get(URL, **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["periods"]), 1)
        self.assertEqual(len(response.data["members"]), 1)
        self.assertEqual(len(response.data["donations"]), 1)

    def test_periods_fields(self):
        response = self.client.get(URL, **self.auth())

        period = response.data["periods"][0]
        self.assertEqual(period["id"], self.period.id)
        self.assertEqual(period["name"], "весна 2024")
        self.assertEqual(period["date"], "2024-03-01")
        self.assertTrue(period["is_active"])

    def test_members_fields(self):
        response = self.client.get(URL, **self.auth())

        member = response.data["members"][0]
        self.assertEqual(member["id"], self.member.id)
        self.assertEqual(member["name"], "Алексей Костров")
        self.assertEqual(member["label"], "Горная школа")

    def test_donations_fields(self):
        response = self.client.get(URL, **self.auth())

        donation = response.data["donations"][0]
        self.assertEqual(donation["member_id"], self.member.id)
        self.assertEqual(donation["period_id"], self.period.id)
        self.assertTrue(donation["is_paid"])
        self.assertEqual(donation["amount"], "1500.00")
        self.assertEqual(donation["paid_date"], "2024-04-10")
        self.assertEqual(donation["recipient"], MemberDonation.RECIPIENT_SBP)
        self.assertEqual(donation["note"], "")

    def test_unpaid_donation_has_null_paid_date(self):
        MemberDonation.objects.create(
            member=ClubMember.objects.create(name="Другой Участник"),
            period=self.period,
            is_paid=False,
        )

        response = self.client.get(URL, **self.auth())

        unpaid = next(d for d in response.data["donations"] if not d["is_paid"])
        self.assertIsNone(unpaid["paid_date"])

    def test_member_with_empty_notes_has_empty_label(self):
        ClubMember.objects.create(name="Без метки")

        response = self.client.get(URL, **self.auth())

        no_label = next(m for m in response.data["members"] if m["name"] == "Без метки")
        self.assertEqual(no_label["label"], "")

    def test_returns_403_without_token(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 403)

    def test_returns_403_with_wrong_token(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION="Bearer wrong-token")
        self.assertEqual(response.status_code, 403)

    def test_returns_403_without_bearer_prefix(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION=TOKEN)
        self.assertEqual(response.status_code, 403)

    @override_settings(CONTRIBUTORS_API_TOKEN=None)
    def test_returns_403_when_token_not_configured(self):
        response = self.client.get(URL, **self.auth())
        self.assertEqual(response.status_code, 403)


@pytest.mark.django_db
def test_checkpoint_api_excludes_hidden_type(client):
    """type=hidden КП must not appear in /api/race/<id>/checkpoint/."""
    from website.models.checkpoint import Checkpoint
    from website.models.enums import CheckpointType
    from website.models.race import Race

    race = Race.objects.create(name="Hidden type test", slug="hidden-type-test-api")
    Checkpoint.objects.create(
        race=race, number=1, cost=5, description="visible", type=CheckpointType.kp.value
    )
    Checkpoint.objects.create(
        race=race,
        number=2,
        cost=9,
        description="secret",
        type=CheckpointType.hidden.value,
    )

    response = client.get(f"/api/race/{race.id}/checkpoint/")

    assert response.status_code == 200
    numbers = [cp["number"] for cp in response.json()]
    assert 1 in numbers
    assert 2 not in numbers


@pytest.mark.django_db
def test_checkpoint_api_nonexistent_race_returns_404(client):
    """Non-existent race_id must return 404, not an empty 200."""
    response = client.get("/api/race/999999/checkpoint/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_checkpoint_api_hides_locked_cp(client):
    """Locked КП must not leak cost/description via /api/ (lock-only, no race flag)."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Lock test", slug="lock-test-api")
    Checkpoint.objects.create(
        race=race, number=1, cost=5, description="secret tree", is_legend_locked=True
    )

    response = client.get(f"/api/race/{race.id}/checkpoint/")

    assert response.status_code == 200
    cp = response.json()[0]
    assert cp["cost"] == 0
    assert cp["description"] == ""


@pytest.mark.django_db
def test_checkpoint_api_exposes_open_cp(client):
    """Open (unlocked) default type=kp КП serves cleartext cost/description.

    Headline behavior change: this previously returned 0/"" when the race had
    is_legend_visible=False — now hiding depends solely on is_legend_locked.
    """
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    race = Race.objects.create(name="Open test", slug="open-test-api")
    Checkpoint.objects.create(
        race=race, number=1, cost=3, description="open spot", is_legend_locked=False
    )

    response = client.get(f"/api/race/{race.id}/checkpoint/")

    assert response.status_code == 200
    cp = response.json()[0]
    assert cp["cost"] == 3
    assert cp["description"] == "open spot"
    assert "color" not in cp


def _make_superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="su", email="su@example.com", password="pw"
    )


@pytest.mark.django_db
def test_checkpoint_tag_create_by_id(client, django_user_model):
    """Legacy api tag-create resolves the КП by id and echoes both ids."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag create", slug="tag-create-api")
    cp = Checkpoint.objects.create(race=race, number=42, cost=5)

    response = client.post(
        f"/api/race/{race.id}/checkpoint_tag/",
        {"checkpoint_id": cp.id, "nfc_uid": "abc123"},
        content_type="application/json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["checkpoint_id"] == cp.id
    assert body["number"] == 42
    assert body["nfc_uid"] == "ABC123"
    assert body["bid"] != ""
    assert body["code"] is not None
    assert "id" not in body
    assert "point" not in body


@pytest.mark.django_db
def test_checkpoint_tag_create_idempotent_same_cp(client, django_user_model):
    """Same UID on the same КП returns 200 (idempotent), not a duplicate."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag idem", slug="tag-idem-api")
    cp = Checkpoint.objects.create(race=race, number=1, cost=5)

    url = f"/api/race/{race.id}/checkpoint_tag/"
    first = client.post(
        url,
        {"checkpoint_id": cp.id, "nfc_uid": "uid1"},
        content_type="application/json",
    )
    second = client.post(
        url,
        {"checkpoint_id": cp.id, "nfc_uid": "uid1"},
        content_type="application/json",
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["checkpoint_id"] == cp.id


@pytest.mark.django_db
def test_checkpoint_tag_create_cross_cp_conflict(client, django_user_model):
    """A UID already bound to a different КП returns 409 (no auto-rebind)."""
    from website.models.checkpoint import Checkpoint
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag conflict", slug="tag-conflict-api")
    cp1 = Checkpoint.objects.create(race=race, number=1, cost=5)
    cp2 = Checkpoint.objects.create(race=race, number=2, cost=5)

    url = f"/api/race/{race.id}/checkpoint_tag/"
    first = client.post(
        url,
        {"checkpoint_id": cp1.id, "nfc_uid": "shared"},
        content_type="application/json",
    )
    assert first.status_code == 201
    response = client.post(
        url,
        {"checkpoint_id": cp2.id, "nfc_uid": "shared"},
        content_type="application/json",
    )

    assert response.status_code == 409
    assert "nfc_uid" in response.json()


@pytest.mark.django_db
def test_checkpoint_tag_create_unknown_id_returns_404(client, django_user_model):
    """An id that does not resolve to a КП in the race returns 404."""
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag 404", slug="tag-404-api")

    response = client.post(
        f"/api/race/{race.id}/checkpoint_tag/",
        {"checkpoint_id": 999999, "nfc_uid": "abc"},
        content_type="application/json",
    )

    assert response.status_code == 404
    assert "checkpoint_id" in response.json()


@pytest.mark.django_db
def test_checkpoint_tag_create_non_superuser_returns_401(client, django_user_model):
    """Non-superuser requests to the disabled endpoint return 401."""
    from website.models.race import Race

    regular = django_user_model.objects.create_user(
        username="reg", email="reg@example.com", password="pw"
    )
    client.force_login(regular)
    race = Race.objects.create(name="Tag auth", slug="tag-auth-api")

    response = client.post(
        f"/api/race/{race.id}/checkpoint_tag/",
        {"checkpoint_id": 1, "nfc_uid": "abc"},
        content_type="application/json",
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_checkpoint_tag_create_missing_checkpoint_id_returns_400(
    client, django_user_model
):
    """Body missing checkpoint_id returns 400 (serializer validation error)."""
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag 400", slug="tag-400-api")

    response = client.post(
        f"/api/race/{race.id}/checkpoint_tag/",
        {"nfc_uid": "abc"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "checkpoint_id" in response.json()


@pytest.mark.django_db
def test_checkpoint_tag_create_rejects_hidden_cp(client, django_user_model):
    """A hidden КП must not be bindable by id (resolved set excludes hidden)."""
    from website.models.checkpoint import Checkpoint
    from website.models.enums import CheckpointType
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag hidden", slug="tag-hidden-api")
    hidden = Checkpoint.objects.create(
        race=race, number=1, cost=5, type=CheckpointType.hidden.value
    )

    response = client.post(
        f"/api/race/{race.id}/checkpoint_tag/",
        {"checkpoint_id": hidden.id, "nfc_uid": "abc"},
        content_type="application/json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_checkpoint_tag_create_repairs_stale_bid_code_on_idempotent(
    client, django_user_model
):
    """Idempotent 200 for an existing row with bid=="" / code=None must repair and
    return valid bid+code instead of an invalid payload."""
    from website.models.checkpoint import Checkpoint, CheckpointTag
    from website.models.race import Race

    client.force_login(_make_superuser(django_user_model))
    race = Race.objects.create(name="Tag repair", slug="tag-repair-api")
    cp = Checkpoint.objects.create(race=race, number=5, cost=5)

    # Create a row that bypassed signals (bid empty, code null) — use QuerySet.update()
    # which issues raw SQL and skips Python save() / post_save signals.
    tag = CheckpointTag.objects.create(checkpoint=cp, nfc_uid="REPAIR01")
    CheckpointTag.objects.filter(pk=tag.pk).update(bid="", code=None)

    response = client.post(
        f"/api/race/{race.id}/checkpoint_tag/",
        {"checkpoint_id": cp.id, "nfc_uid": "REPAIR01"},
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bid"] != ""
    assert body["code"] is not None
