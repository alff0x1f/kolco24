"""Throttling for the mobile per-person endpoints.

DRF's stock ``ScopedRateThrottle.get_ident`` trusts ``X-Forwarded-For`` only as
far as ``NUM_PROXIES`` is configured; with ``NUM_PROXIES`` unset it takes the
**first** XFF entry, which a client can forge to rotate its throttle key and get
a fresh bucket per request. This subclass instead derives the rate-limit
identity from :func:`apps.mobile.permissions._client_ip`, which prefers the
``X-Real-IP`` header set by nginx (after its ``real_ip`` module resolves the
actual client address from XFF) and falls back to the last XFF entry only in
environments without nginx (local runserver, tests). It does not read
``request.data``/``request.body``, so it keeps the body-read ordering guarantee
the plain ``ScopedRateThrottle`` had.
"""

from rest_framework.throttling import ScopedRateThrottle

from .permissions import _client_ip


class ClientIPScopedRateThrottle(ScopedRateThrottle):
    """Scoped throttle keyed by the un-spoofable client IP."""

    def get_ident(self, request):
        return _client_ip(request) or "0.0.0.0"
