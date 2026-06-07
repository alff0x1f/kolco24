"""Resource version fingerprints for the mobile sync manifest + ETags.

``teams_version`` is the **single source of truth** for the teams resource
version: ``TeamsView`` wraps it in quotes for the strong ``ETag`` header, and
``SyncView`` emits the bare value in the manifest's ``versions.teams``. Keep the
two consumers reading from this one helper so an ETag and a manifest probe can
never disagree.

``legend_version`` is the matching single source of truth for the legend
resource: ``LegendView`` wraps it in quotes for the ``ETag`` and ``SyncView``
emits the bare value in ``versions.legend``.
"""

import hashlib

from django.db.models import Count, Max

from website.models.checkpoint import Checkpoint
from website.models.enums import CheckpointType
from website.models.models import Athlet, Team
from website.models.race import Race


def teams_version(race_id):
    """Return a short, stable fingerprint of a race's teams + members.

    Combines ``MAX(Team.updated_at)|MAX(Athlet.updated_at)|COUNT(Athlet)|COUNT(Team)``
    (``TeamManager`` already excludes ``is_deleted``) so a team edit, a member
    rename, a member add/remove, or a team add/remove all move the fingerprint.
    Teams with ``category2=None`` are out of scope (a race owns teams via
    ``category2.race``) and excluded by the filter.

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
    raw = (
        f"{teams['max_updated']}|{members['max_updated']}"
        f"|{members['count']}|{teams['count']}"
    )
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


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
    """
    agg = (
        Checkpoint.objects.filter(race_id=race_id)
        .exclude(type=CheckpointType.draft.value)
        .aggregate(max_updated=Max("updated_at"), count=Count("id"))
    )
    # Deliberately re-query is_legend_visible by race_id rather than accepting the
    # view's Race object: legend_version keeps a bare race_id signature to stay the
    # single source of truth for both the ETag and versions.legend. Do not
    # "optimize" this into a Race parameter — it would break that contract.
    visible = (
        Race.objects.filter(pk=race_id)
        .values_list("is_legend_visible", flat=True)
        .first()
    )
    raw = f"{agg['max_updated']}|{agg['count']}|{visible}"
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()
