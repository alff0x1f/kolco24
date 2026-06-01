from datetime import timedelta

import pytest
from django.core import mail
from django.core.signing import TimestampSigner
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.emails import build_magic_link_signature, send_login_email
from apps.accounts.models import EmailVerification

LOCMEM_EMAIL = "django.core.mail.backends.locmem.EmailBackend"


def _is_logged_in(client):
    return "_auth_user_id" in client.session


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
    # accepting does not consume and does not increment attempts
    obj.refresh_from_db()
    assert obj.consumed_at is None
    assert obj.attempts == 0


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
    # the old row must be revoked so its magic link can no longer be used
    first.refresh_from_db()
    assert first.consumed_at is not None


@pytest.mark.django_db
def test_create_for_after_cooldown_revokes_older_unconsumed_rows():
    first, _ = EmailVerification.create_for("user@example.com")
    EmailVerification.objects.filter(pk=first.pk).update(
        created_at=timezone.now()
        - EmailVerification.RESEND_COOLDOWN
        - timedelta(seconds=1)
    )

    EmailVerification.create_for("user@example.com")

    first.refresh_from_db()
    assert first.consumed_at is not None
    assert not first.is_alive


@pytest.mark.django_db
def test_create_for_after_expiry_issues_new_row_without_constraint_error():
    # Regression: an expired unconsumed row must not block a fresh create_for().
    # The unique partial index covers consumed_at IS NULL regardless of expires_at,
    # so the expired row must be revoked before the INSERT, not after.
    first, _ = EmailVerification.create_for("user@example.com")
    EmailVerification.objects.filter(pk=first.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    second, raw_code2 = EmailVerification.create_for("user@example.com")

    assert second.pk != first.pk
    assert raw_code2 is not None
    assert second.is_alive
    first.refresh_from_db()
    assert first.consumed_at is not None


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


# ── Passwordless views: start ──────────────────────────────────────────


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_start_post_creates_one_row_and_queues_one_email(client):
    response = client.post(reverse("account_start"), {"email": "new@example.com"})

    assert response.status_code == 302
    assert response.url == reverse("account_verify")
    assert EmailVerification.objects.count() == 1
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "/accounts/link/" in body  # magic link
    assert any(c.isdigit() for c in body)  # the code
    # the pending email is stashed in session for the verify step
    assert client.session["accounts_pending_email"] == "new@example.com"


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_start_is_neutral_for_known_and_unknown_email(client, django_user_model):
    django_user_model.objects.create_user("known", "known@example.com", "pw")

    known = client.post(reverse("account_start"), {"email": "known@example.com"})
    client.logout()
    unknown = client.post(reverse("account_start"), {"email": "nobody@example.com"})

    # identical response regardless of whether the account exists
    assert known.status_code == unknown.status_code == 302
    assert known.url == unknown.url == reverse("account_verify")


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_start_normalizes_email_before_storing(client):
    client.post(reverse("account_start"), {"email": "Mixed@Example.COM"})

    assert client.session["accounts_pending_email"] == "mixed@example.com"
    assert EmailVerification.objects.get().email == "mixed@example.com"


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_resend_within_cooldown_queues_no_second_email(client):
    client.post(reverse("account_start"), {"email": "user@example.com"})
    assert len(mail.outbox) == 1

    response = client.post(reverse("account_verify"), {"action": "resend"})

    assert response.status_code == 302
    # cooldown: no new row, no second email
    assert EmailVerification.objects.count() == 1
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_authed_user_at_start_is_bounced_to_next(client, django_user_model):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    client.login(username="u@example.com", password="pw")

    response = client.get(reverse("account_start") + "?next=/race/foo/")

    assert response.status_code == 302
    assert response.url == "/race/foo/"


@pytest.mark.django_db
def test_start_post_invalid_email_rerenders_form(client):
    response = client.post(reverse("account_start"), {"email": "not-an-email"})

    assert response.status_code == 200
    assert EmailVerification.objects.count() == 0


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
@pytest.mark.django_db
def test_start_post_stores_next_url_in_session(client):
    response = client.post(
        reverse("account_start"), {"email": "u@example.com", "next": "/race/foo/"}
    )

    assert response.status_code == 302
    assert client.session["accounts_next"] == "/race/foo/"


# ── Passwordless views: verify (code path) ─────────────────────────────


@pytest.mark.django_db
def test_verify_get_no_session_redirects_to_start(client):
    response = client.get(reverse("account_verify"))

    assert response.status_code == 302
    assert response.url == reverse("account_start")


@pytest.mark.django_db
def test_verify_get_authenticated_user_redirects(client, django_user_model):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    client.login(username="u@example.com", password="pw")

    response = client.get(reverse("account_verify"))

    assert response.status_code == 302
    assert response.url == "/"


@pytest.mark.django_db
def test_verify_post_no_session_redirects_to_start(client):
    response = client.post(reverse("account_verify"), {"code": "123456"})

    assert response.status_code == 302
    assert response.url == reverse("account_start")


@pytest.mark.django_db
def test_verify_post_locked_out_row_is_rejected(client, django_user_model):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, raw_code = EmailVerification.create_for("u@example.com")
    EmailVerification.objects.filter(pk=obj.pk).update(
        attempts=EmailVerification.MAX_ATTEMPTS
    )
    session = client.session
    session["accounts_pending_email"] = "u@example.com"
    session.save()

    response = client.post(reverse("account_verify"), {"code": raw_code})

    assert response.status_code == 200
    assert not _is_logged_in(client)


@pytest.mark.django_db
def test_verify_correct_code_logs_in_existing_user_and_honors_next(
    client, django_user_model
):
    user = django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, raw_code = EmailVerification.create_for("u@example.com")
    session = client.session
    session["accounts_pending_email"] = "u@example.com"
    session["accounts_next"] = "/race/foo/"
    session.save()

    response = client.post(reverse("account_verify"), {"code": raw_code})

    assert response.status_code == 302
    assert response.url == "/race/foo/"
    assert client.session["_auth_user_id"] == str(user.pk)
    obj.refresh_from_db()
    assert obj.consumed_at is not None


@pytest.mark.django_db
def test_verify_unknown_email_creates_user_and_profile_and_logs_in(
    client, django_user_model
):
    obj, raw_code = EmailVerification.create_for("fresh@example.com")
    session = client.session
    session["accounts_pending_email"] = "fresh@example.com"
    session["accounts_next"] = ""
    session.save()

    response = client.post(reverse("account_verify"), {"code": raw_code})

    assert response.status_code == 302
    user = django_user_model.objects.get(email="fresh@example.com")
    assert _is_logged_in(client)
    # the profile is auto-created by the existing signal
    assert user.profile is not None


@pytest.mark.django_db
def test_verify_wrong_code_increments_attempts_and_does_not_log_in(
    client, django_user_model
):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, raw_code = EmailVerification.create_for("u@example.com")
    wrong = "000000" if raw_code != "000000" else "111111"
    session = client.session
    session["accounts_pending_email"] = "u@example.com"
    session.save()

    response = client.post(reverse("account_verify"), {"code": wrong})

    assert response.status_code == 200
    assert not _is_logged_in(client)
    obj.refresh_from_db()
    assert obj.attempts == 1


@pytest.mark.django_db
def test_verify_expired_row_is_rejected(client, django_user_model):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, raw_code = EmailVerification.create_for("u@example.com")
    obj.expires_at = timezone.now() - timedelta(seconds=1)
    obj.save(update_fields=["expires_at"])
    session = client.session
    session["accounts_pending_email"] = "u@example.com"
    session.save()

    response = client.post(reverse("account_verify"), {"code": raw_code})

    assert response.status_code == 200
    assert not _is_logged_in(client)


@pytest.mark.django_db
def test_verify_inactive_user_is_not_logged_in(client, django_user_model):
    user = django_user_model.objects.create_user("u", "u@example.com", "pw")
    user.is_active = False
    user.save()
    obj, raw_code = EmailVerification.create_for("u@example.com")
    session = client.session
    session["accounts_pending_email"] = "u@example.com"
    session.save()

    client.post(reverse("account_verify"), {"code": raw_code})

    assert not _is_logged_in(client)


# ── Passwordless views: magic link ─────────────────────────────────────


@pytest.mark.django_db
def test_magic_link_valid_logs_in_and_honors_next(client, django_user_model):
    user = django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, _ = EmailVerification.create_for("u@example.com")
    signed = build_magic_link_signature(obj)

    response = client.get(reverse("magic_link", args=[signed]) + "?next=/race/bar/")

    assert response.status_code == 302
    assert response.url == "/race/bar/"
    assert client.session["_auth_user_id"] == str(user.pk)
    obj.refresh_from_db()
    assert obj.consumed_at is not None


@pytest.mark.django_db
def test_magic_link_unknown_email_creates_user(client, django_user_model):
    obj, _ = EmailVerification.create_for("link-new@example.com")
    signed = build_magic_link_signature(obj)

    response = client.get(reverse("magic_link", args=[signed]))

    assert response.status_code == 302
    assert django_user_model.objects.filter(email="link-new@example.com").exists()
    assert _is_logged_in(client)


@pytest.mark.django_db
def test_magic_link_tampered_signature_is_rejected(client):
    obj, _ = EmailVerification.create_for("u@example.com")
    signed = build_magic_link_signature(obj)

    response = client.get(reverse("magic_link", args=[signed + "x"]))

    assert response.status_code == 404
    assert not _is_logged_in(client)


@pytest.mark.django_db
def test_magic_link_expired_row_is_rejected(client):
    obj, _ = EmailVerification.create_for("u@example.com")
    obj.expires_at = timezone.now() - timedelta(seconds=1)
    obj.save(update_fields=["expires_at"])
    signed = build_magic_link_signature(obj)

    response = client.get(reverse("magic_link", args=[signed]))

    assert response.status_code == 404
    assert not _is_logged_in(client)


@pytest.mark.django_db
def test_magic_link_consumed_or_reused_row_is_rejected(client, django_user_model):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, _ = EmailVerification.create_for("u@example.com")
    signed = build_magic_link_signature(obj)

    first = client.get(reverse("magic_link", args=[signed]))
    assert first.status_code == 302
    client.logout()
    # reusing the same link now hits a consumed row
    second = client.get(reverse("magic_link", args=[signed]))

    assert second.status_code == 404
    assert not _is_logged_in(client)


@pytest.mark.django_db
def test_magic_link_inactive_user_is_not_logged_in(client, django_user_model):
    user = django_user_model.objects.create_user("u", "u@example.com", "pw")
    user.is_active = False
    user.save()
    obj, _ = EmailVerification.create_for("u@example.com")
    signed = build_magic_link_signature(obj)

    client.get(reverse("magic_link", args=[signed]))

    assert not _is_logged_in(client)


@pytest.mark.django_db
def test_magic_link_off_host_next_falls_back_to_root(client, django_user_model):
    django_user_model.objects.create_user("u", "u@example.com", "pw")
    obj, _ = EmailVerification.create_for("u@example.com")
    signed = build_magic_link_signature(obj)

    response = client.get(
        reverse("magic_link", args=[signed]) + "?next=http://evil.com/x"
    )

    assert response.status_code == 302
    assert response.url == "/"
    assert _is_logged_in(client)


# ── EmailBackend ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_email_backend_rejects_inactive_user(django_user_model):
    from apps.accounts.backends import EmailBackend

    user = django_user_model.objects.create_user("u", "inactive@example.com", "pw")
    user.is_active = False
    user.save()

    result = EmailBackend().authenticate(
        None, username="inactive@example.com", password="pw"
    )

    assert result is None


@pytest.mark.django_db
def test_email_backend_authenticates_active_user(django_user_model):
    from apps.accounts.backends import EmailBackend

    django_user_model.objects.create_user("u", "active@example.com", "pw")

    result = EmailBackend().authenticate(
        None, username="active@example.com", password="pw"
    )

    assert result is not None
    assert result.email == "active@example.com"


# ── AddTeam anon redirect → passwordless start ──────────────────────────


@pytest.mark.django_db
def test_add_team_anon_get_redirects_to_account_start(client):
    from website.models import Race

    race = Race.objects.create(name="Anon Race", code=900, slug="anon-race")
    add_url = reverse("add_team", args=[race.slug])

    resp = client.get(add_url)

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("account_start"))
    assert f"next={add_url}" in resp.url
    assert not resp.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_add_team_anon_post_redirects_to_account_start(client):
    from website.models import Race

    race = Race.objects.create(name="Anon Race", code=901, slug="anon-race-post")
    add_url = reverse("add_team", args=[race.slug])

    resp = client.post(add_url, {})

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("account_start"))
    assert f"next={add_url}" in resp.url
    assert not resp.url.startswith(reverse("login"))
