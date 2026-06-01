import logging
from urllib.parse import urlencode

from django.core.mail import EmailMultiAlternatives
from django.core.signing import TimestampSigner
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)

FROM_EMAIL = "Кольцо24 <org@kolco24.ru>"


def build_magic_link_signature(verification):
    """Sign the verification pk for embedding in the magic link URL."""
    return TimestampSigner().sign(str(verification.pk))


def send_login_email(request, verification, code, next_url=""):
    """Queue the login email carrying both the 6-digit code and a magic link.

    The magic link embeds ``TimestampSigner().sign(str(pk))``; ``next`` rides along
    as an unsigned query param (validated later by ``_safe_redirect``). A send
    failure is logged but does not raise — the user can still finish via the code.
    """
    signed = build_magic_link_signature(verification)
    path = reverse("magic_link", args=[signed])
    if next_url:
        path = f"{path}?{urlencode({'next': next_url})}"
    magic_link_url = request.build_absolute_uri(path)

    context = {
        "code": code,
        "magic_link_url": magic_link_url,
        "next_url": next_url,
    }
    subject = render_to_string("accounts/email/login_code_subject.txt", context).strip()
    text_body = render_to_string("accounts/email/login_code.txt", context)
    html_body = render_to_string("accounts/email/login_code.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=FROM_EMAIL,
        to=[verification.email],
    )
    message.attach_alternative(html_body, "text/html")
    try:
        message.send()
    except Exception:
        logger.exception("Failed to send login email to %s", verification.email)
