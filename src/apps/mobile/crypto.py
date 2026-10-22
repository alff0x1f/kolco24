"""AES-256-GCM + HKDF primitives for legend envelope encryption.

Ported from ``scratch/playground.py``. No master key / at-rest wrapping — the DB
is trusted in this threat model, so we encrypt only what leaves the server. The
stored blobs carry just ``{"iv", "ct"}`` (the playground's ``{"v", "alg"}`` tags
are intentionally dropped: a single fixed format, YAGNI here).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def seal(key: bytes, plaintext: bytes, aad: bytes) -> dict:
    """AES-256-GCM encrypt ``plaintext`` under ``key`` → ``{"iv": b64, "ct": b64}``.

    ``ct`` is ``ciphertext || tag(16)``; a fresh random 12-byte IV per call.
    """
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, aad)
    return {"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def unseal(key: bytes, enc: dict, aad: bytes) -> bytes:
    """Decrypt a ``{"iv", "ct"}`` blob produced by :func:`seal`.

    Raises ``cryptography.exceptions.InvalidTag`` on a wrong key, wrong ``aad``,
    or tampered ciphertext. Not named ``open`` (would shadow the builtin).
    """
    return AESGCM(key).decrypt(
        base64.b64decode(enc["iv"]), base64.b64decode(enc["ct"]), aad
    )


def derive_wrap_key(code: bytes, info: bytes = b"kp-wrap-v1") -> bytes:
    """Derive a 32-byte wrap key from a high-entropy NFC ``code`` via HKDF-SHA256.

    HKDF here is for domain separation / length normalization, not brute-force
    resistance — ``code`` is ``os.urandom(16)``, so no Argon2 is needed.
    """
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(code)


MEMBER_CODE_BYTES = 12
MEMBER_FIELD_MAX = 0xFFFF
# The record's packed version/type byte (version 2, type participant). It leads
# the HMAC message so a participant code never verifies as another chip type
# signed with the same race key.
MEMBER_RECORD_TYPE = 0x22


def member_code(key: bytes, nfc_uid: str, race_id: int, number: int) -> bytes:
    """Bracelet code of a K24 v2 participant record (12 bytes).

    ``HMAC-SHA256(key, 0x22 ‖ uid_bytes ‖ race_id u16 BE ‖ number u16 BE)[:12]``
    — the judge's phone computes the same value offline and writes it to the chip.
    ``nfc_uid`` is the hex UID, normalized here. Raises ``ValueError`` on a
    non-hex UID or a ``race_id``/``number`` outside ``1..65535``.
    """
    for name, value in (("race_id", race_id), ("number", number)):
        if not 1 <= value <= MEMBER_FIELD_MAX:
            raise ValueError(f"{name} out of range: {value}")
    uid = bytes.fromhex(nfc_uid.strip().upper())
    if not uid:
        raise ValueError("empty nfc_uid")
    msg = (
        bytes([MEMBER_RECORD_TYPE])
        + uid
        + race_id.to_bytes(2, "big")
        + number.to_bytes(2, "big")
    )
    return hmac.new(key, msg, hashlib.sha256).digest()[:MEMBER_CODE_BYTES]
