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

from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from website.models.checkpoint import Checkpoint, CheckpointSecret, CheckpointTag

from .legend_crypto import TAG_UPDATE_FIELDS, build_bundle, seal_checkpoint

# The update_fields set build_bundle writes with — a post_save carrying exactly
# this set is the service's own write and must not re-trigger a rebuild.
SENTINEL_UPDATE_FIELDS = frozenset(TAG_UPDATE_FIELDS)

_guard = threading.local()


def _build_bundle_guarded(tag):
    """Run ``build_bundle`` under a thread-local re-entrancy fence."""
    if getattr(_guard, "active", False):
        return
    _guard.active = True
    try:
        build_bundle(tag)
    finally:
        _guard.active = False


@receiver(post_save, sender=Checkpoint, dispatch_uid="mobile_seal_checkpoint")
def checkpoint_saved(sender, instance, **kwargs):
    had_secret = CheckpointSecret.objects.filter(checkpoint=instance).exists()
    seal_checkpoint(instance)
    # A lock toggle is exactly the case where secret-existence and the flag
    # disagreed before sealing: open→locked (key appeared) or locked→open (key
    # disappeared). A cleartext-only edit (locked→locked) leaves them equal and
    # touches only enc_blob, so dependent bundles keep the same content_key.
    if had_secret != bool(instance.is_legend_locked):
        dependents = set(instance.tags.all()) | set(instance.unlocked_by.all())
        for tag in dependents:
            _build_bundle_guarded(tag)


@receiver(post_save, sender=CheckpointTag, dispatch_uid="mobile_build_bundle")
def checkpointtag_saved(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and set(update_fields) == SENTINEL_UPDATE_FIELDS:
        # build_bundle's own write — do not recurse.
        return
    _build_bundle_guarded(instance)


@receiver(
    m2m_changed,
    sender=CheckpointTag.unlocks.through,
    dispatch_uid="mobile_unlocks_changed",
)
def checkpointtag_unlocks_changed(sender, instance, action, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if isinstance(instance, CheckpointTag):
        tags = [instance]
    else:
        # Reverse relation (``checkpoint.unlocked_by.add(tag)``): instance is a
        # Checkpoint; the affected tags are in pk_set (None on post_clear).
        if pk_set:
            tags = list(CheckpointTag.objects.filter(pk__in=pk_set))
        else:
            tags = list(instance.unlocked_by.all())
    for tag in tags:
        _build_bundle_guarded(tag)
