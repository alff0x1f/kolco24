"""Opaque bearer-token helpers for the mobile per-person login.

The raw token is a high-entropy ``secrets.token_urlsafe(32)`` string; only its
sha256 hex digest is persisted (``MobileToken.token_hash``). Lookup hashes the
presented bearer and hits the indexed column — see the rationale on
``MobileToken``.
"""

import hashlib
import secrets

from django.utils import timezone

from .models import MobileToken


def hash_token(raw):
    """Return the sha256 hex digest used as ``MobileToken.token_hash``."""
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_token():
    """Mint a fresh ``(raw, token_hash)`` pair. The raw is shown to the client once."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def resolve_token(raw):
    """Resolve a presented raw bearer to its active ``MobileToken``, or ``None``.

    Returns ``None`` for unknown / expired / revoked tokens. On success stamps
    ``last_used_at`` best-effort (a stamp failure never blocks the lookup).
    """
    if not raw:
        return None
    try:
        token = MobileToken.objects.get(token_hash=hash_token(raw))
    except MobileToken.DoesNotExist:
        return None
    if not token.is_active:
        return None
    token.last_used_at = timezone.now()
    try:
        token.save(update_fields=["last_used_at"])
    except Exception:  # pragma: no cover - best-effort audit stamp
        pass
    return token
