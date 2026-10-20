"""Per-race key for K24 v2 participant bracelet codes.

The cloud server is the only place a key is minted; the LAN server gets it with
the DB copy (``backup_db``/``restore_db``), so the two can never disagree.
"""

from __future__ import annotations

import os

from website.models.race import Race

MEMBER_KEY_BYTES = 32


def get_member_key(race_id: int) -> bytes | None:
    key = Race.objects.filter(pk=race_id).values_list("member_key", flat=True).first()
    return bytes(key) if key is not None else None


def ensure_member_key(race_id: int) -> bytes | None:
    """Return the race key, minting it if missing (``None`` for an unknown race).

    A conditional ``UPDATE … WHERE member_key IS NULL`` instead of a row lock:
    two concurrent calls write at most once and both re-read the winner. It goes
    around ``save()`` on purpose — ``Race.updated_at`` feeds the races ETag, and
    a key the app never sees in that list must not move it.
    """
    Race.objects.filter(pk=race_id, member_key__isnull=True).update(
        member_key=os.urandom(MEMBER_KEY_BYTES)
    )
    return get_member_key(race_id)
