"""DRF permission enforcing the HMAC-signed mobile-app request contract.

The check is pure crypto: it selects the per-build secret from the keyed map
``MOBILE_APP_KEYS`` (by the request's ``X-App-Key-Id``), verifies the signature
over a canonical string and, on success, stashes parsed metadata on
``request.app_meta`` for the view to record stats. It does no DB writes itself.
It fails closed — an empty/missing ``MOBILE_APP_KEYS`` (or an unknown key-id)
rejects every request — and returns no hint about which check failed (the view
turns any ``False`` into a neutral 403).
"""

import ipaddress
import time

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from website.models.race import Race

from .signing import build_canonical, verify
from .tokens import resolve_token


class MobileTokenInvalid(APIException):
    """Actionable 401 for a missing/expired/revoked bearer token.

    With ``authentication_classes = []`` on :class:`AppAPIView`, raising DRF's
    own ``NotAuthenticated``/``AuthenticationFailed`` renders as **403** (DRF
    has no authenticator to attach a ``WWW-Authenticate`` header, so it
    downgrades the status). To emit a genuine **401** — distinct from the
    neutral build-layer 403 — :class:`IsMobileUser` raises this small
    ``APIException`` subclass with an explicit ``status_code``.
    """

    status_code = 401
    default_detail = "Недействительный или просроченный токен"
    default_code = "token_invalid"


def _client_ip(request):
    """Trusted client IP: last ``X-Forwarded-For`` entry, else ``REMOTE_ADDR``.

    Nginx's ``proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for``
    *appends* ``$remote_addr`` to whatever the client sent, so the last entry
    is the actual connection IP and cannot be forged by the client.  Taking the
    first entry would accept an attacker-supplied value.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        candidate = forwarded.split(",")[-1].strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            pass
    return request.META.get("REMOTE_ADDR") or None


class SignedAppPermission(BasePermission):
    """Verify the mobile app's HMAC signature; stash metadata on success."""

    # Neutral denial: never hint which check failed (don't help brute-forcing).
    message = "Forbidden"

    def _deny(self, request, reason, key_id=""):
        """Stash the denial reason for the view to log/record, then ``return False``.

        Stays pure — no DB write. ``key_id`` is the *claimed* id (may be spoofed
        or ``None``); coerce ``None`` → ``""`` so the missing-headers/unknown-key
        paths can't ``TypeError`` on a length-clamp downstream.
        """
        install = request.headers.get("X-Install-Id") or ""
        request.app_denial = {
            "reason": reason,
            "key_id": (key_id or "")[:32],
            # Sentinel "0.0.0.0" avoids NULL in the unique_together — PostgreSQL
            # treats NULL != NULL in unique constraints so two concurrent null-IP
            # denials would both INSERT, then MultipleObjectsReturned breaks
            # subsequent update_or_create for the same (NULL, key_id, reason).
            "ip": _client_ip(request) or "0.0.0.0",
            "path": request.get_full_path()[:255],
            "install": install[:64],
        }
        return False

    def has_permission(self, request, view):
        keys = getattr(settings, "MOBILE_APP_KEYS", {}) or {}
        if not keys:
            # fail closed: misconfigured deploy never leaks the legend
            return self._deny(request, "no_keys")

        key_id = request.headers.get("X-App-Key-Id")
        sig = request.headers.get("X-App-Sig")
        ts = request.headers.get("X-App-Ts")
        install = request.headers.get("X-Install-Id")
        if not key_id or not sig or not ts or not install:
            # Normalise key_id to "" — the claimed id is untrusted and must not
            # vary the aggregation key, or an attacker can force a row per request
            # by rotating X-App-Key-Id while omitting other required headers.
            return self._deny(request, "missing_headers", "")

        secret = keys.get(key_id)
        if not secret:
            # Normalise key_id to "" — the claimed id is untrusted and must not
            # vary the aggregation key, or an attacker can force a row per request.
            return self._deny(request, "unknown_key", "")  # no hint

        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            return self._deny(request, "bad_ts", key_id)

        window = getattr(settings, "MOBILE_APP_TS_WINDOW", 300)
        if abs(int(time.time()) - ts_int) > window:
            return self._deny(request, "expired_ts", key_id)

        canonical = build_canonical(
            request.method, request.get_full_path(), ts, request.body
        )
        if not verify(secret, canonical, sig):
            return self._deny(request, "bad_sig", key_id)

        request.app_meta = {
            "install_id": install[:64],
            "platform": request.headers.get("X-App-Platform", "")[:16],
            "app_version": request.headers.get("X-App-Version", "")[:32],
            # Store the full key_id — views use it for MOBILE_APP_KEYS lookup and
            # legend fingerprinting.  DB column truncation (max_length=32) happens
            # in _record_install, not here.
            "key_id": key_id,
            "ip": _client_ip(request),
        }
        return True


class IsMobileUser(BasePermission):
    """Resolve ``Authorization: Bearer <token>`` to ``request.mobile_user``.

    Identity only — it authorizes **nothing** (per-action authorization is
    :class:`CanEditRaceLegend`). Stack it **after** :class:`SignedAppPermission`
    so the per-build HMAC is already proven; on success it sets
    ``request.mobile_user`` (the :class:`~django.contrib.auth` user) and
    ``request.mobile_token`` (the resolved :class:`MobileToken`, for a later
    revoke).

    Unlike the neutral build layer, a token failure is **actionable**: a missing
    header, a malformed ``Authorization`` value, or an unknown/expired/revoked
    token raises :class:`MobileTokenInvalid` → HTTP **401** (see that class for
    why a raised exception, not ``return False``, is required to get a 401 here).
    """

    def has_permission(self, request, view):
        auth = request.headers.get("Authorization", "")
        parts = auth.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise MobileTokenInvalid()
        token = resolve_token(parts[1])
        if token is None:
            raise MobileTokenInvalid()
        request.mobile_user = token.user
        request.mobile_token = token
        return True


class CanEditRaceLegend(BasePermission):
    """Authorize the resolved mobile user to edit a race's legend (per-race).

    Reads ``view.kwargs["race_id"]``, loads the :class:`Race` (a missing race
    raises ``Http404`` → HTTP **404**, not a 403, so a probe can't distinguish
    "no such race" from "not allowed" any differently than the web editor does),
    and delegates to :func:`apps.race.permissions.can_edit_race` — the **same**
    superuser-or-``RaceAdmin(role=ADMIN)`` check the web ``RaceLegendEditView``
    uses. A user without rights gets an **actionable 403** (the default DRF
    ``PermissionDenied`` message), unlike the neutral build-layer 403.

    **Ordering:** stack this **after** :class:`IsMobileUser`, which sets
    ``request.mobile_user``. The user is read defensively via
    ``getattr(request, "mobile_user", None)`` so a reordered stack, or the
    permission tested in isolation, returns ``False`` (→ 403) rather than
    raising ``AttributeError`` (→ 500).
    """

    def has_permission(self, request, view):
        from apps.race.permissions import can_edit_race

        user = getattr(request, "mobile_user", None)
        if user is None:
            return False
        race = get_object_or_404(Race, pk=view.kwargs["race_id"])
        return can_edit_race(user, race)
