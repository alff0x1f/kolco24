import datetime
from decimal import Decimal

import pytest
from django.test import override_settings
from rest_framework.test import APITestCase

from donate.models import ClubMember, DonationPeriod, MemberDonation

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

    def test_returns_403_with_empty_bearer_token(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION="Bearer ")
        self.assertEqual(response.status_code, 403)

    def test_returns_403_with_non_ascii_token(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION="Bearer тест")
        self.assertEqual(response.status_code, 403)

    @override_settings(CONTRIBUTORS_API_TOKEN="")
    def test_returns_403_when_token_is_empty_string(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION="Bearer ")
        self.assertEqual(response.status_code, 403)

    def test_returns_403_without_bearer_prefix(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION=TOKEN)
        self.assertEqual(response.status_code, 403)

    @override_settings(CONTRIBUTORS_API_TOKEN=None)
    def test_returns_403_when_token_not_configured(self):
        response = self.client.get(URL, **self.auth())
        self.assertEqual(response.status_code, 403)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/api/member_tag/",
        "/api/member_tag/touch/",
        "/api/race/1/checkpoint/",
        "/api/race/1/checkpoint_tag/",
        "/api/race/1/teams/",
        "/api/race/1/teams.csv",
    ],
)
def test_removed_api_routes_return_404(client, path):
    assert client.get(path).status_code == 404
    assert client.post(path, {}).status_code == 404


def test_ping_returns_pong(client):
    response = client.get("/api/ping/")
    assert response.status_code == 200
    assert response.content == b"[pong]"
