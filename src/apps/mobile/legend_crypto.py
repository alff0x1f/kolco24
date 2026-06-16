"""Service layer: precompute the stored legend-encryption blobs.

These three functions are the only place that writes ``CheckpointSecret`` and
the ``CheckpointTag`` crypto columns (``code``/``bid``/``bundle_blob``). They are
driven by signals (Task 4) and the backfill/repair management command (Task 5).

Envelope scheme (see ``docs/plans/20260615-legend-encryption.md`` / the
``scratch/playground.py`` prototype):

- each locked КП is sealed with its own random ``content_key`` (AES-256-GCM over
  ``{cost, description}``, ``aad = str(cp.id)``);
- each NFC tag carries a random ``code``; the per-tag ``bundle_blob`` is an
  AES-GCM of ``{cp_id: content_key}`` over the tag's unlock set, wrapped with
  ``HKDF(code)`` and bound to ``aad = bid``.

The DB is trusted in this threat model, so ``content_key`` and ``code`` are
stored raw.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from website.models.enums import CheckpointType

from .crypto import derive_wrap_key, seal

# The exact update_fields set the CheckpointTag writes use — also the recursion
# sentinel the Task 4 post_save signal early-returns on. Keep in sync there.
TAG_UPDATE_FIELDS = ["code", "bid", "bundle_blob", "updated_at"]


def seal_checkpoint(cp):
    """Keep a locked КП's ``CheckpointSecret`` consistent with its cleartext.

    Unlocked КП have no secret: an existing one is deleted. Locked КП get a
    secret whose ``enc_blob`` is re-sealed from the current ``{cost, description}``.
    The ``content_key`` is generated once and **preserved** across description
    edits (regenerating it would invalidate every bundle that carries it).
    """
    from website.models.checkpoint import CheckpointSecret

    if not cp.is_legend_locked:
        CheckpointSecret.objects.filter(checkpoint=cp).delete()
        return None

    plaintext = json.dumps(
        {"cost": cp.cost, "description": cp.description},
        ensure_ascii=False,
    ).encode()
    # Pre-compute a fresh key+blob so the defaults row is never written with an
    # empty enc_blob — avoids a brief window where a concurrent legend fetch
    # would read {"enc": {}} (missing iv/ct) and crash the mobile app.
    fresh_key = os.urandom(32)
    fresh_blob = seal(fresh_key, plaintext, aad=str(cp.id).encode())

    secret, created = CheckpointSecret.objects.get_or_create(
        checkpoint=cp,
        defaults={"content_key": fresh_key, "enc_blob": fresh_blob},
    )
    if created:
        return secret

    # Existing secret: preserve content_key, re-seal with current plaintext.
    if not secret.content_key:
        secret.content_key = os.urandom(32)
    secret.enc_blob = seal(
        bytes(secret.content_key), plaintext, aad=str(cp.id).encode()
    )
    secret.save(update_fields=["content_key", "enc_blob", "updated_at"])
    return secret


def ensure_code(tag):
    """Assign a fresh random 16-byte ``code`` only when the tag has none.

    Regenerating an existing code would break tags already written in the field,
    so this is a no-op once a code is present.
    """
    if tag.code is None:
        tag.code = os.urandom(16)
    return tag


def build_bundle(tag):
    """(Re)compute ``code``/``bid``/``bundle_blob`` for a tag.

    The unlock set is ``tag.unlocks`` (M2M) or — when empty — its own КП
    (``[tag.point]`` runtime default). Only **locked** КП in that set contribute
    a ``content_key``; open КП are skipped (they have no secret).
    """
    from website.models.checkpoint import CheckpointSecret

    ensure_code(tag)
    code = bytes(tag.code)

    # Resolve the unlock set: empty M2M → implicit [point] default; non-empty M2M
    # → filter to same-race non-hidden КП only (cross-race or hidden entries are
    # dropped; if all are invalid the filtered list stays empty → bundle_blob=None,
    # not a silent fallback to [point] which would grant an unconfigured key).
    if not tag.unlocks.exists():
        unlocked = [tag.point]
    elif tag.point.race_id is None:
        # Orphaned tag (race deleted, race_id=None): filter(race_id=None) would
        # match all orphaned checkpoints across deleted races — drop unlocks instead.
        unlocked = []
    else:
        unlocked = list(
            tag.unlocks.filter(race_id=tag.point.race_id).exclude(
                type=CheckpointType.hidden.value
            )
        )

    secrets = {
        s.checkpoint_id: s
        for s in CheckpointSecret.objects.filter(
            checkpoint__in=[cp.id for cp in unlocked]
        )
    }
    bundle = {
        str(cp_id): base64.b64encode(bytes(secret.content_key)).decode()
        for cp_id, secret in secrets.items()
    }

    bid = hashlib.sha256(code).hexdigest()[:16]
    tag.bid = bid
    if bundle:
        tag.bundle_blob = seal(
            derive_wrap_key(code), json.dumps(bundle).encode(), aad=bid.encode()
        )
    else:
        # No locked КП in the unlock set — nothing to protect. The tag still
        # appears in the /legend/ tags response for identity; only bid="" rows are
        # excluded by the view (.exclude(bid="")).
        tag.bundle_blob = None
    tag.save(update_fields=TAG_UPDATE_FIELDS)
    return tag
