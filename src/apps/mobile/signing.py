"""HMAC-SHA256 request signing for the mobile app endpoints.

Mirrors the client side baked into the iOS/Android binaries: the app builds the
same canonical string from the request and signs it with the per-build shared
secret selected by ``X-App-Key-Id``. The canonical signs the *hash* of the body
(not the body itself) to keep the signed string small.
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


def tag_hash(secret: str, nfc_uid: str) -> str:
    """HMAC-SHA256 hexdigest of ``nfc_uid`` keyed by the per-build ``secret``.

    Mirrors the client side: the app hashes a scanned NFC UID string with the
    same per-build secret and compares against the ``tag_hash`` values served in
    the legend, so the raw ``nfc_uid`` never travels on the wire.
    """
    return hmac.new(secret.encode(), nfc_uid.encode(), hashlib.sha256).hexdigest()
