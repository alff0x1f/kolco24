from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import EmailVerification


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
