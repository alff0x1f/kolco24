"""Resource version fingerprints for the mobile sync manifest + ETags.

``teams_version`` is the **single source of truth** for the teams resource
version: ``TeamsView`` wraps it in quotes for the strong ``ETag`` header, and
``SyncView`` emits the bare value in the manifest's ``versions.teams``. Keep the
two consumers reading from this one helper so an ETag and a manifest probe can
never disagree.

``legend_version`` is the matching single source of truth for the legend
resource: ``LegendView`` wraps it in quotes for the ``ETag`` and ``SyncView``
emits the bare value in ``versions.legend``.

``races_version`` is the single source of truth for the global published-races
list served by ``RaceListView``. It is global (no ``race_id``) and deliberately
**not** part of the per-race ``SyncView`` manifest — the races list is the
app's entry point, probed directly via its own conditional GET.
"""

import hashlib

from django.db.models import Count, Max

from website.models.checkpoint import Checkpoint
from website.models.enums import CheckpointType
from website.models.models import Athlet, Team
from website.models.race import Category, Race


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


def legend_state(race_id):
    """Return ``(version, is_legend_visible)`` paired within one call.

    Both values are computed together so the view uses a single, consistent
    pair — a visibility flip between two independent calls cannot produce a
    response body that contradicts its own ETag.  There is no DB-level
    snapshot: the two queries (checkpoint aggregate and race row) are
    independent, so a flip between them yields a version that represents
    neither state. Self-corrects on the next request.

    Deliberately re-queries ``is_legend_visible`` by ``race_id`` rather than
    accepting a ``Race`` object: keeps the bare ``race_id`` signature that is
    the single source of truth for both the ETag and ``versions.legend``. Do
    not "optimize" this into a ``Race`` parameter — it would break that
    contract.
    """
    agg = (
        Checkpoint.objects.filter(race_id=race_id)
        .exclude(type=CheckpointType.draft.value)
        .aggregate(max_updated=Max("updated_at"), count=Count("id"))
    )
    visible = (
        Race.objects.filter(pk=race_id)
        .values_list("is_legend_visible", flat=True)
        .first()
    )
    raw = f"{agg['max_updated']}|{agg['count']}|{visible}"
    version = hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()
    return version, visible


def legend_version(race_id):
    """Return a short, stable fingerprint of a race's legend.

    Combines ``MAX(Checkpoint.updated_at)|COUNT(Checkpoint)|is_legend_visible``
    over the **draft-excluded** queryset the legend view actually serves, so a
    checkpoint edit, add/remove, a ``kp <-> draft`` flip (``COUNT`` moves), or a
    hide/show of the legend all move the fingerprint. A draft-checkpoint edit
    deliberately does **not** move it (drafts are not in the response), and tags
    (``CheckpointTag``) are out of scope (the legend never exposes them).

    None aggregates (empty/all-draft race) render as the literal ``"None"`` →
    stable, non-crashing. Returns **bare** hex (no quotes).

    Thin wrapper around :func:`legend_state` — use ``legend_state`` when you
    also need the ``is_legend_visible`` flag (e.g. in :class:`LegendView`) to
    avoid a second independent DB read that could race with a visibility flip.
    """
    version, _ = legend_state(race_id)
    return version
