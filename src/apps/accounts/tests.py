from datetime import timedelta

import pytest
from django.core import mail
from django.core.signing import TimestampSigner
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.accounts.emails import send_login_email
from apps.accounts.models import EmailVerification

LOCMEM_EMAIL = "django.core.mail.backends.locmem.EmailBackend"


@pytest.mark.django_db
def test_create_for_issues_hashed_six_digit_code():
    obj, raw_code = EmailVerification.create_for("user@example.com")

    assert raw_code is not None
    assert len(raw_code) == 6
    assert raw_code.isdigit()
    # raw code is never stored — only the hash
    assert obj.code_hash != raw_code
    assert obj.code_hash
    assert obj.purpose == "login"
    assert obj.attempts == 0
    assert obj.consumed_at is None
    assert obj.is_alive


@pytest.mark.django_db
def test_create_for_normalizes_email_lowercase():
    obj, _ = EmailVerification.create_for("User@Example.COM")

    assert obj.email == "user@example.com"


@pytest.mark.django_db
def test_verify_code_accepts_correct_code():
    obj, raw_code = EmailVerification.create_for("user@example.com")

    assert obj.verify_code(raw_code) is True
    # accepting does not consume
    obj.refresh_from_db()
    assert obj.consumed_at is None


@pytest.mark.django_db
def test_verify_code_rejects_wrong_code_and_increments_attempts():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    wrong = "000000" if raw_code != "000000" else "111111"

    assert obj.verify_code(wrong) is False
    assert obj.attempts == 1
    obj.refresh_from_db()
    assert obj.attempts == 1


@pytest.mark.django_db
def test_verify_code_rejects_after_max_attempts():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    wrong = "000000" if raw_code != "000000" else "111111"

    for _ in range(EmailVerification.MAX_ATTEMPTS):
        obj.verify_code(wrong)

    assert obj.attempts == EmailVerification.MAX_ATTEMPTS
    assert obj.is_alive is False
    # even the right code is now rejected (dead row)
    assert obj.verify_code(raw_code) is False


@pytest.mark.django_db
def test_verify_code_rejects_when_expired():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    obj.expires_at = timezone.now() - timedelta(seconds=1)
    obj.save(update_fields=["expires_at"])

    assert obj.is_alive is False
    assert obj.verify_code(raw_code) is False


@pytest.mark.django_db
def test_verify_code_rejects_when_consumed():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    obj.mark_consumed()

    assert obj.is_alive is False
    assert obj.verify_code(raw_code) is False


@pytest.mark.django_db
def test_mark_consumed_is_idempotent():
    obj, _ = EmailVerification.create_for("user@example.com")
    obj.mark_consumed()
    first = obj.consumed_at
    obj.mark_consumed()

    assert obj.consumed_at == first


@pytest.mark.django_db
def test_create_for_within_cooldown_reuses_row_without_new_code():
    first, raw_code = EmailVerification.create_for("user@example.com")
    assert raw_code is not None

    second, raw_code2 = EmailVerification.create_for("user@example.com")

    assert second.pk == first.pk
    assert raw_code2 is None
    assert EmailVerification.objects.count() == 1


@pytest.mark.django_db
def test_create_for_after_cooldown_issues_new_row():
    first, _ = EmailVerification.create_for("user@example.com")
    # age the first row past the cooldown
    EmailVerification.objects.filter(pk=first.pk).update(
        created_at=timezone.now()
        - EmailVerification.RESEND_COOLDOWN
        - timedelta(seconds=1)
    )

    second, raw_code2 = EmailVerification.create_for("user@example.com")

    assert second.pk != first.pk
    assert raw_code2 is not None
    assert EmailVerification.objects.count() == 2


@pytest.mark.django_db
def test_create_for_after_consumed_issues_new_row():
    first, _ = EmailVerification.create_for("user@example.com")
    first.mark_consumed()

    second, raw_code2 = EmailVerification.create_for("user@example.com")

    assert second.pk != first.pk
    assert raw_code2 is not None


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_send_login_email_queues_one_message_with_code_and_link():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    request = RequestFactory().get("/accounts/start/")

    send_login_email(request, obj, raw_code, next_url="/race/foo/")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["user@example.com"]
    # plain-text body carries the code and the link
    assert raw_code in message.body
    assert "/accounts/link/" in message.body
    # an HTML alternative is attached
    assert any(alt[1] == "text/html" for alt in message.alternatives)
    html = message.alternatives[0][0]
    assert raw_code in html


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_send_login_email_link_round_trips_to_row_pk():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    request = RequestFactory().get("/accounts/start/")

    send_login_email(request, obj, raw_code, next_url="/race/foo/")

    body = mail.outbox[0].body
    # extract the signed token between /accounts/link/ and the next slash
    marker = "/accounts/link/"
    start = body.index(marker) + len(marker)
    signed = body[start:].split("/", 1)[0]

    assert TimestampSigner().unsign(signed) == str(obj.pk)


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_send_login_email_includes_next_query_param():
    obj, raw_code = EmailVerification.create_for("user@example.com")
    request = RequestFactory().get("/accounts/start/")

    send_login_email(request, obj, raw_code, next_url="/race/foo/")

    assert "next=%2Frace%2Ffoo%2F" in mail.outbox[0].body


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_send_login_email_swallows_send_failure(monkeypatch):
    obj, raw_code = EmailVerification.create_for("user@example.com")
    request = RequestFactory().get("/accounts/start/")

    def boom(self):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)

    # must not raise
    send_login_email(request, obj, raw_code, next_url="/race/foo/")
