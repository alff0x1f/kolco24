"""Per-race key of K24 v2 chip codes, and the check of participant codes.

The cloud server is the only place a key is minted; the LAN server gets it with
the DB copy (``backup_db``/``restore_db``), so the two can never disagree.
"""

from __future__ import annotations

import hmac
import os

from website.models.race import Race
from website.models.tag import Tag

from .crypto import CHIP_CODE_BYTES, member_code
from .models import MarkPresent, RaceChipKey

CHIP_KEY_BYTES = 32
V1_CODE_BYTES = 16


def get_chip_key(race_id: int) -> bytes | None:
    key = (
        RaceChipKey.objects.filter(race_id=race_id)
        .values_list("key", flat=True)
        .first()
    )
    return bytes(key) if key is not None else None


def ensure_chip_key(race_id: int) -> bytes | None:
    """Return the race key, minting it if missing (``None`` for an unknown race).

    ``get_or_create`` on the race's primary key: two concurrent calls create at
    most one row and both read the winner. Never call this on the LAN server.
    """
    if not Race.objects.filter(pk=race_id).exists():
        return None
    row, _ = RaceChipKey.objects.get_or_create(
        race_id=race_id, defaults={"key": os.urandom(CHIP_KEY_BYTES)}
    )
    return bytes(row.key)


def _parse_code(code):
    try:
        return bytes.fromhex((code or "").strip())
    except ValueError:
        return None


def v1_codes_for(presents):
    """``{nfc_uid: code}`` of global (v1) tags for the presents carrying a v1 code.

    ``presents`` are ``MarkPresent`` rows. One query for the whole upload batch;
    feed the result to :func:`check_member_code`.
    """
    uids = set()
    for p in presents:
        code = _parse_code(p.code)
        uid = (p.nfc_uid or "").strip().upper()
        if code is not None and len(code) == V1_CODE_BYTES and uid:
            uids.add(uid)
    if not uids:
        return {}
    rows = Tag.objects.filter(race__isnull=True, nfc_uid__in=uids).values_list(
        "nfc_uid", "code"
    )
    return {uid: bytes(code) for uid, code in rows if code is not None}


def check_member_code(race_id, key, nfc_uid, number, code, v1_codes):
    """Whether a present member's bracelet code is genuine.

    A 12-byte code is K24 v2 — recomputed from the race ``key``; a 16-byte code
    is K24 v1 — compared with the global ``Tag.code`` (``v1_codes``). Returns
    ``None`` when there is nothing to check (no code / uid, unknown v1 tag),
    ``False`` for a mismatch, a bad code, or a v2 code in a race with no key.
    """
    uid = (nfc_uid or "").strip().upper()
    if not uid or not code:
        return None
    raw = _parse_code(code)
    if raw is None:
        return False
    if len(raw) == CHIP_CODE_BYTES:
        if key is None:
            return False
        try:
            expected = member_code(key, uid, race_id, number)
        except ValueError:
            return False
        return hmac.compare_digest(expected, raw)
    if len(raw) == V1_CODE_BYTES:
        stored = v1_codes.get(uid)
        if stored is None:
            return None
        return hmac.compare_digest(stored, raw)
    return False


def recheck_present_codes(race_id, mark_ids):
    """Recompute ``code_ok`` of every stored present row of ``mark_ids``.

    Run after the upload's insert, against the stored rows: a re-sent mark keeps
    its existing rows (``ignore_conflicts``) while its race may have changed, so
    a value computed only for newly inserted rows could go stale.
    """
    stored = list(MarkPresent.objects.filter(mark_id__in=mark_ids))
    if not stored:
        return
    key = get_chip_key(race_id)
    v1_codes = v1_codes_for(stored)
    changed = []
    for p in stored:
        ok = check_member_code(race_id, key, p.nfc_uid, p.number, p.code, v1_codes)
        if ok is not p.code_ok:
            p.code_ok = ok
            changed.append(p)
    if changed:
        MarkPresent.objects.bulk_update(changed, ["code_ok"])
