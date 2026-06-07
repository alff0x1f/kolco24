"""HMAC-SHA256 request signing for the mobile app endpoints.

Mirrors the client side baked into the iOS/Android binaries: the app builds the
same canonical string from the request and signs it with the shared
``MOBILE_APP_SECRET``. The canonical signs the *hash* of the body (not the body
itself) to keep the signed string small.
"""

import hashlib
import hmac


def sha256_hex(body: bytes) -> str:
    """Hex SHA-256 of the raw request body (empty body → hash of ``b""``)."""
    return hashlib.sha256(body).hexdigest()


def build_canonical(method: str, full_path: str, ts: str, body: bytes) -> str:
    """Build the canonical string that both sides sign.

    ``method.upper() + "\\n" + full_path + "\\n" + ts + "\\n" + sha256_hex(body)``
    """
    return "\n".join([method.upper(), full_path, ts, sha256_hex(body)])


def sign(secret: str, canonical: str) -> str:
    """HMAC-SHA256 hexdigest of ``canonical`` keyed by ``secret``."""
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def verify(secret: str, canonical: str, provided_sig: str) -> bool:
    """Constant-time comparison of the expected signature against the provided one."""
    try:
        return hmac.compare_digest(sign(secret, canonical), provided_sig.lower())
    except (TypeError, ValueError):
        return False
