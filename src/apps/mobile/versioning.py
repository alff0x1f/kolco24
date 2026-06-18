"""Resource version fingerprints for the mobile sync manifest + ETags.

``teams_version`` is the **single source of truth** for the teams resource
version: ``TeamsView`` wraps it in quotes for the strong ``ETag`` header, and
``SyncView`` emits the bare value in the manifest's ``versions.teams``. Keep the
two consumers reading from this one helper so an ETag and a manifest probe can
never disagree.

``legend_version`` is the matching single source of truth for the legend
resource: ``LegendView`` wraps it in quotes for the ``ETag`` and ``SyncView``
emits the bare value in ``versions.legend``. The legend is now
**build-independent** (the ciphertext/bundles are precomputed and stored, no
longer keyed by the per-build secret), so the fingerprint folds in three
aggregates — ``Checkpoint``, ``CheckpointSecret`` (re-seal / enc appear or
disappear), and ``CheckpointTag`` (code/unlocks/bundle/check_method) — and
takes **no** ``key_id``. Two builds therefore share the legend ETag.
``teams_version``/``races_version`` are unaffected.

``races_version`` is the single source of truth for the global published-races
list served by ``RaceListView``. It is global (no ``race_id``) and deliberately
**not** part of the per-race ``SyncView`` manifest — the races list is the
app's entry point, probed directly via its own conditional GET.
"""

import hashlib
import json
from datetime import timedelta

from django.db.models import Count, Max, Q

from website.models.checkpoint import Checkpoint, CheckpointSecret, CheckpointTag
from website.models.enums import CheckpointType
from website.models.models import Athlet, Team
from website.models.race import Category, Race
from website.models.tag import Tag


def teams_version(race_id):
    """Return a short, stable fingerprint of a race's teams + members + categories.

    Combines
    ``MAX(Team.updated_at)|MAX(Athlet.updated_at)|COUNT(Athlet)|COUNT(Team)|MAX(Category.updated_at)|COUNT(Category)``
    (``TeamManager`` already excludes ``is_deleted``) so a team edit, a member
    rename, a member add/remove, or a team add/remove all move the fingerprint.
    Teams with ``category2=None`` are out of scope (a race owns teams via
    ``category2.race``) and excluded by the filter.

    Categories are folded in because they ride inside the teams response (no
    separate ``versions.categories``): a category **rename/reorder** moves
    ``MAX(Category.updated_at)`` (its ``updated_at`` is ``auto_now``) and a
    category **add/delete** moves ``COUNT(Category)``. The category aggregate is
    over ``Category.objects.filter(race_id=race_id)`` with **no ``is_active``
    filter** — the exact queryset the view serves (single-source contract).

    None aggregates (empty race) render as the literal ``"None"`` → stable,
    non-crashing. Returns **bare** hex (no quotes).
    """
    teams = Team.objects.filter(category2__race_id=race_id).aggregate(
        max_updated=Max("updated_at"),
        count=Count("id"),
    )
    members = Athlet.objects.filter(
        team__is_deleted=False, team__category2__race_id=race_id
    ).aggregate(
        max_updated=Max("updated_at"),
        count=Count("id"),
    )
    categories = Category.objects.filter(race_id=race_id).aggregate(
        max_updated=Max("updated_at"),
        count=Count("id"),
    )
    raw = (
        f"{teams['max_updated']}|{members['max_updated']}"
        f"|{members['count']}|{teams['count']}"
        f"|{categories['max_updated']}|{categories['count']}"
    )
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


def races_version():
    """Return a short, stable fingerprint of the published-races list.

    Combines ``MAX(Race.updated_at)|COUNT`` over
    ``Race.objects.filter(is_published=True)`` — the exact queryset
    ``RaceListView`` serves (single-source contract). An edit to a published
    race moves ``MAX(updated_at)`` (``auto_now``); a publish/unpublish moves
    ``COUNT`` (so an unpublish is detected even when the race wasn't the
    ``MAX``). An edit to an *unpublished* race deliberately does not move the
    fingerprint — it is not in the response.

    None aggregate (no published races) renders as the literal ``"None"`` →
    stable, non-crashing. Returns **bare** hex (no quotes).
    """
    agg = Race.objects.filter(is_published=True).aggregate(
        max_updated=Max("updated_at"),
        count=Count("id"),
    )
    raw = f"{agg['max_updated']}|{agg['count']}"
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


def legend_version(race_id):
    """Return a short, stable fingerprint of a race's legend.

    Folds in three ``MAX(updated_at)|COUNT`` aggregates over the **non-hidden**
    checkpoints of ``race_id`` (the same hidden-exclusion predicate the legend
    view serves):

    1. ``Checkpoint`` — a checkpoint edit, add/remove, a ``kp <-> hidden`` flip
       (``COUNT`` moves), or a lock toggle.
    2. ``CheckpointSecret`` — a re-seal, or an ``enc`` blob appearing/disappearing
       as a КП is locked/unlocked.
    3. ``CheckpointTag`` — a code/unlocks/bundle/check_method change.

    A hidden-checkpoint edit (and a tag on a hidden checkpoint) deliberately does
    **not** move it (hidden КП are not in the response). The legend is
    **build-independent** (the stored ciphertext/bundles do not depend on the
    per-build secret), so the fingerprint takes **no** ``key_id`` and two builds
    share the ETag.

    None aggregates (empty/all-hidden/tag-less race) render as the literal
    ``"None"`` → stable, non-crashing. Returns **bare** hex (no quotes).
    """
    agg = (
        Checkpoint.objects.filter(race_id=race_id)
        .exclude(type=CheckpointType.hidden.value)
        .aggregate(max_updated=Max("updated_at"), count=Count("id"))
    )
    secrets = (
        CheckpointSecret.objects.filter(checkpoint__race_id=race_id)
        .exclude(checkpoint__type=CheckpointType.hidden.value)
        .aggregate(max_updated=Max("updated_at"), count=Count("id"))
    )
    tags = (
        CheckpointTag.objects.filter(point__race_id=race_id)
        .exclude(point__type=CheckpointType.hidden.value)
        .aggregate(max_updated=Max("updated_at"), count=Count("id"))
    )
    raw = (
        f"{agg['max_updated']}|{agg['count']}"
        f"|{secrets['max_updated']}|{secrets['count']}"
        f"|{tags['max_updated']}|{tags['count']}"
    )
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


def active_member_tags():
    """Return the member-tag (participant bracelet) pool the mobile endpoint serves.

    Uses a **data-anchored** 30-day window rather than wall-clock ``now()``:
    the floor is ``MAX(last_seen_at) - 30 days``, so an idle race has a perfectly
    stable served set (the floor only advances with real scan activity). A
    never-scanned pool (``MAX(last_seen_at) is None``) returns the whole pool.

    This is the **single source** feeding both ``MemberTagsView`` and
    ``member_tags_version`` so the ETag can never disagree with the body. The
    pool is **global** today (``Tag`` has no race FK — one chip set is physically
    reused across races); ``race_id`` will be threaded through here once per-race
    chip sets exist.
    """
    newest = Tag.objects.aggregate(max_seen=Max("last_seen_at"))["max_seen"]
    if newest is None:
        return Tag.objects.all()
    return Tag.objects.filter(
        Q(last_seen_at__isnull=True) | Q(last_seen_at__gte=newest - timedelta(days=30))
    )


def member_tags_version(rows=None):
    """Return a short, stable fingerprint of the member-tag pool.

    Fetches ``(id, number, nfc_uid)`` for every tag in ``active_member_tags()``
    (the exact queryset the view serves — single-source contract), serialises the
    list as canonical JSON (so any special characters in ``nfc_uid`` are escaped
    and the encoding is unambiguous), then hashes with ``blake2b``. Hashing the
    **actual served field values** means any provisioning edit — renumber,
    re-UID, add, remove — is detected regardless of how concurrent writes order
    their timestamps. A same-COUNT identity swap (one tag ages out while a touch
    brings another in, leaving ``MAX(updated_at)`` and COUNT unchanged) is also
    caught because different ``id`` values appear in the hash.

    A **provisioning** edit always moves the fingerprint: **add** inserts a new
    ``id:number:nfc_uid`` tuple; **renumber** or **re-UID** changes a field
    value in an existing tuple; **remove** drops a tuple. A scan (``touch``)
    deliberately does **not** — ``MemberTagTouchView`` saves only
    ``last_seen_at`` (an intentional carve-out from the ``update_fields``
    discipline) so a bracelet tap cannot churn this version on its own. Scan
    activity can still shift the fingerprint *gradually* (day-scale) when it
    advances ``MAX(last_seen_at)`` enough to age chips past the 30-day floor
    (membership change, not per-scan churn).

    Like ``races_version`` this is **global** (no ``race_id``). **Unlike**
    ``races_version`` it **is** included in the per-race ``SyncView`` manifest:
    it is served at a per-race URL (``/app/race/<id>/member_tags/``), so the app
    needs one sync poll to learn whether to refetch the pool for the race it is
    syncing.

    Pass pre-fetched ``rows`` (a list of ``(id, number, nfc_uid)`` tuples,
    ordered by ``id``) to avoid a second DB round-trip when the caller has
    already materialised the queryset for serialisation (enforcing the
    single-snapshot contract). When ``rows`` is ``None`` the function queries
    ``active_member_tags()`` itself.

    Empty pool yields a stable ``"empty"``-based hash. Returns **bare** hex (no
    quotes).
    """
    if rows is None:
        rows = list(
            active_member_tags().order_by("id").values_list("id", "number", "nfc_uid")
        )
    if not rows:
        raw = "empty"
    else:
        # json.dumps encodes the list of tuples as a list of lists and properly
        # escapes any special characters in nfc_uid (e.g. internal whitespace from
        # a direct-DB write that bypassed the save() normalizer), eliminating any
        # ambiguity that a plain newline-joined format would allow.
        raw = json.dumps(rows)
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()
