"""DRF permission enforcing the HMAC-signed mobile-app request contract.

The check is pure crypto: it verifies the shared-secret signature over a
canonical string and, on success, stashes parsed metadata on ``request.app_meta``
for the view to record stats. It does no DB writes itself. It fails closed —
an empty/missing ``MOBILE_APP_SECRET`` rejects every request — and returns no
hint about which check failed (the view turns any ``False`` into a neutral 403).
"""

import ipaddress
import time

from django.conf import settings
from rest_framework.permissions import BasePermission

from .signing import build_canonical, verify


def _client_ip(request):
    """Best-effort client IP: first ``X-Forwarded-For`` entry, else ``REMOTE_ADDR``."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
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

    def has_permission(self, request, view):
        secret = getattr(settings, "MOBILE_APP_SECRET", "") or ""
        if not secret:
            return False  # fail closed: misconfigured deploy never leaks the legend

        sig = request.headers.get("X-App-Sig")
        ts = request.headers.get("X-App-Ts")
        install = request.headers.get("X-Install-Id")
        if not sig or not ts or not install:
            return False

        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            return False

        window = getattr(settings, "MOBILE_APP_TS_WINDOW", 300)
        if abs(int(time.time()) - ts_int) > window:
            return False

        canonical = build_canonical(
            request.method, request.get_full_path(), ts, request.body
        )
        if not verify(secret, canonical, sig):
            return False

        request.app_meta = {
            "install_id": install[:64],
            "platform": request.headers.get("X-App-Platform", "")[:16],
            "app_version": request.headers.get("X-App-Version", "")[:32],
            "ip": _client_ip(request),
        }
        return True
