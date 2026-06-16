"""AES-256-GCM + HKDF primitives for legend envelope encryption.

Ported from ``scratch/playground.py``. No master key / at-rest wrapping — the DB
is trusted in this threat model, so we encrypt only what leaves the server. The
stored blobs carry just ``{"iv", "ct"}`` (the playground's ``{"v", "alg"}`` tags
are intentionally dropped: a single fixed format, YAGNI here).
"""

from __future__ import annotations

import base64
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
