from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from donate.models import DonateRequest


class DonateViewTests(TestCase):
    @patch("donate.views.VTBClient.create_order")
    def test_creates_donate_request_with_sender_and_comment(self, create_order_mock):
        create_order_mock.return_value = {
            "object": {
                "orderId": "SPUTNIK_TEST001",
                "orderCode": "ORDERCODE001",
                "amount": {"value": "1500.00", "code": "RUB"},
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
        response = self.client.post(
            reverse("donate"),
            data={
                "amount": "1500",
                "sender_name": "Иванов Иван",
                "comment": "ГШ 2 полугодие 2025",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://pay.example/sbp")
        self.assertEqual(DonateRequest.objects.count(), 1)

        donate_request = DonateRequest.objects.get()
        self.assertEqual(donate_request.sender_name, "Иванов Иван")
        self.assertEqual(donate_request.comment, "ГШ 2 полугодие 2025")

    @patch("donate.views.VTBClient.create_order")
    def test_creates_donate_request_with_text_comment(self, create_order_mock):
        create_order_mock.return_value = {
            "object": {
                "orderId": "SPUTNIK_TEST002",
                "orderCode": "ORDERCODE002",
                "amount": {"value": "2000.00", "code": "RUB"},
                "status": {
                    "value": "CREATED",
                    "description": "CREATED",
                    "changedAt": "2026-03-08T12:00:00Z",
                },
                "createdAt": "2026-03-08T11:59:00Z",
                "expire": "2026-03-09T11:59:00Z",
                "payUrl": "https://pay.example/sbp-custom",
                "preparedPayments": [],
            }
        }
        response = self.client.post(
            reverse("donate"),
            data={
                "amount": "2000",
                "sender_name": "Петров Петр",
                "comment": "Целевой взнос на школу",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://pay.example/sbp-custom")
        donate_request = DonateRequest.objects.get(payment__order_id="SPUTNIK_TEST002")
        self.assertEqual(donate_request.comment, "Целевой взнос на школу")


def _order_payload():
    return {
        "object": {
            "orderId": "SPUTNIK_TEST003",
            "orderCode": "ORDERCODE003",
            "amount": {"value": "1500.00", "code": "RUB"},
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


DONATE_FORM = {"amount": "1500", "sender_name": "Иванов", "comment": "Взнос"}


@pytest.mark.django_db
@patch("donate.views.VTBClient.create_order")
def test_donate_sends_email_of_logged_in_user(create_order_mock, client):
    create_order_mock.return_value = _order_payload()
    user = User.objects.create_user(
        username="donor", password="pass", email="donor@example.com"
    )
    client.force_login(user)

    client.post(reverse("donate"), data=DONATE_FORM)

    assert create_order_mock.call_args.kwargs["customer"] == {
        "email": "donor@example.com"
    }


@pytest.mark.django_db
@patch("donate.views.VTBClient.create_order")
def test_donate_omits_customer_for_anonymous(create_order_mock, client):
    create_order_mock.return_value = _order_payload()

    client.post(reverse("donate"), data=DONATE_FORM)

    assert create_order_mock.call_args.kwargs["customer"] is None
