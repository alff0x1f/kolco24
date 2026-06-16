"""Keep the stored legend-encryption blobs consistent on every model edit.

These receivers are the runtime triggers for the :mod:`legend_crypto` service
layer; the management command (Task 5) is the bulk/backfill path that bypasses
signals. Registered from :meth:`MobileConfig.ready`.

Receivers:

- ``post_save(Checkpoint)`` re-seals the КП's ``CheckpointSecret``. On a lock
  **toggle** (a ``content_key`` just appeared or disappeared) it also rebuilds
  the bundles of every dependent tag — ``cp.tags.all()`` **∪**
  ``cp.unlocked_by.all()``. The ``∪ cp.tags`` half is required because a tag
  with an empty ``unlocks`` M2M unlocks its own КП via the ``[point]`` runtime
  default and is therefore **not** in ``cp.unlocked_by``.
- ``post_save(CheckpointTag)`` and ``m2m_changed(CheckpointTag.unlocks)``
  rebuild that tag's bundle.

Recursion is fenced two ways: the ``post_save(CheckpointTag)`` receiver
early-returns when ``update_fields`` is exactly the service's sentinel set
(``build_bundle``'s own write), and a thread-local flag guards every
``build_bundle`` call so the ``m2m_changed`` path cannot re-enter itself.
"""

from __future__ import annotations

import threading

from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)
from django.dispatch import receiver

from website.models.checkpoint import Checkpoint, CheckpointSecret, CheckpointTag

from .legend_crypto import TAG_UPDATE_FIELDS, build_bundle, seal_checkpoint

# The update_fields set build_bundle writes with — a post_save carrying exactly
# this set is the service's own write and must not re-trigger a rebuild.
SENTINEL_UPDATE_FIELDS = frozenset(TAG_UPDATE_FIELDS)

_guard = threading.local()
_pre_clear_pks = threading.local()
_pre_delete_tags = threading.local()
# Captures (type, race_id) of a Checkpoint before save so checkpoint_saved can
# detect type/race changes that affect bundle inclusion without an old-value API.
_pre_save_cp_state = threading.local()


def _build_bundle_guarded(tag):
    """Run ``build_bundle`` under a thread-local re-entrancy fence."""
    if getattr(_guard, "active", False):
        return
    _guard.active = True
    try:
        build_bundle(tag)
    finally:
        _guard.active = False


@receiver(pre_save, sender=Checkpoint, dispatch_uid="mobile_checkpoint_pre_save")
def checkpoint_pre_save(sender, instance, **kwargs):
    # Capture type and race_id before the save so checkpoint_saved can detect
    # changes that affect whether this checkpoint contributes to bundles.
    if instance.pk:
        try:
            row = Checkpoint.objects.values("type", "race_id").get(pk=instance.pk)
            _pre_save_cp_state.value = row
        except Checkpoint.DoesNotExist:
            _pre_save_cp_state.value = None
    else:
        _pre_save_cp_state.value = None


@receiver(post_save, sender=Checkpoint, dispatch_uid="mobile_seal_checkpoint")
def checkpoint_saved(sender, instance, **kwargs):
    # Skip completely when the save touches only fields unrelated to the legend
    # (e.g. the reg_status OPEN→SOLD_OUT flip in check_vtb_payments) — avoids
    # one EXISTS query per unrelated Checkpoint.save().
    update_fields = kwargs.get("update_fields")
    LEGEND_RELEVANT = {"is_legend_locked", "cost", "description", "type", "race_id"}
    if update_fields is not None and not (set(update_fields) & LEGEND_RELEVANT):
        _pre_save_cp_state.value = None  # discard stale pre-save capture
        return

    had_secret = CheckpointSecret.objects.filter(checkpoint=instance).exists()
    seal_checkpoint(instance)
    # A lock toggle is exactly the case where secret-existence and the flag
    # disagreed before sealing: open→locked (key appeared) or locked→open (key
    # disappeared). A cleartext-only edit (locked→locked) leaves them equal and
    # touches only enc_blob, so dependent bundles keep the same content_key.
    lock_toggled = had_secret != bool(instance.is_legend_locked)

    # Also rebuild when a locked checkpoint's draft↔non-draft status (or race)
    # changes: build_bundle filters explicit unlocks to same-race non-draft КП,
    # so flipping either dimension while the KP is already locked causes stale
    # bundles for tags that carry this checkpoint in their unlocks M2M.
    old_state = getattr(_pre_save_cp_state, "value", None)
    _pre_save_cp_state.value = None
    bundle_filter_changed = (
        instance.is_legend_locked
        and old_state is not None
        and (
            (old_state["type"] == "draft") != (instance.type == "draft")
            or old_state["race_id"] != instance.race_id
        )
    )

    if lock_toggled or bundle_filter_changed:
        dependents = set(instance.tags.select_related("point").all()) | set(
            instance.unlocked_by.select_related("point").all()
        )
        for tag in dependents:
            _build_bundle_guarded(tag)


@receiver(pre_delete, sender=Checkpoint, dispatch_uid="mobile_checkpoint_pre_delete")
def checkpoint_pre_delete(sender, instance, **kwargs):
    # Capture PKs of tags that listed this checkpoint in their unlocks M2M before
    # the DB CASCADE deletes the junction rows (after which unlocked_by is empty).
    # Keyed by checkpoint pk so that a QuerySet.delete() of N checkpoints (which
    # fires all pre_delete signals before any row is deleted) does not overwrite
    # earlier captures — each checkpoint's dependent tags are stored separately.
    # PKs rather than instances: post_delete re-queries so cascade-deleted tags
    # (e.g. a tag whose point checkpoint is in the same bulk delete) are skipped.
    by_cp = getattr(_pre_delete_tags, "by_cp", None)
    if by_cp is None:
        _pre_delete_tags.by_cp = {}
        by_cp = _pre_delete_tags.by_cp
    by_cp[instance.pk] = list(instance.unlocked_by.values_list("pk", flat=True))


@receiver(post_delete, sender=Checkpoint, dispatch_uid="mobile_checkpoint_post_delete")
def checkpoint_post_delete(sender, instance, **kwargs):
    # Rebuild bundles for this checkpoint's captured tags; its junction rows are
    # gone so build_bundle will exclude it from the new bundle. Re-querying by PK
    # naturally skips any CheckpointTag rows that were cascade-deleted (e.g. when
    # the tag's own point checkpoint was part of the same bulk delete).
    by_cp = getattr(_pre_delete_tags, "by_cp", None) or {}
    pks = by_cp.pop(instance.pk, [])
    if not by_cp:
        _pre_delete_tags.by_cp = None
    if pks:
        tags = list(CheckpointTag.objects.select_related("point").filter(pk__in=pks))
        for tag in tags:
            _build_bundle_guarded(tag)


@receiver(post_save, sender=CheckpointTag, dispatch_uid="mobile_build_bundle")
def checkpointtag_saved(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and set(update_fields) == SENTINEL_UPDATE_FIELDS:
        # build_bundle's own write — do not recurse.
        return
    # Re-fetch with select_related so build_bundle can access tag.point without
    # an extra query — post_save instances may not have the FK relation cached.
    tag = CheckpointTag.objects.select_related("point").get(pk=instance.pk)
    _build_bundle_guarded(tag)


@receiver(
    m2m_changed,
    sender=CheckpointTag.unlocks.through,
    dispatch_uid="mobile_unlocks_changed",
)
def checkpointtag_unlocks_changed(sender, instance, action, pk_set, **kwargs):
    # On a reverse pre_clear (checkpoint.unlocked_by.clear()), capture the
    # affected tag PKs *before* Django deletes the junction rows so that the
    # matching post_clear can still find and rebuild those tags.
    if action == "pre_clear" and not isinstance(instance, CheckpointTag):
        _pre_clear_pks.value = list(instance.unlocked_by.values_list("pk", flat=True))
        return

    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if isinstance(instance, CheckpointTag):
        tags = [instance]
    else:
        # Reverse relation: instance is a Checkpoint; affected tags in pk_set
        # (None on post_clear — rows are already deleted by the time it fires).
        if action == "post_clear":
            pks = getattr(_pre_clear_pks, "value", None) or []
            _pre_clear_pks.value = None
            tags = list(
                CheckpointTag.objects.select_related("point").filter(pk__in=pks)
            )
        elif pk_set:
            tags = list(
                CheckpointTag.objects.select_related("point").filter(pk__in=pk_set)
            )
        else:
            tags = []
    for tag in tags:
        _build_bundle_guarded(tag)
