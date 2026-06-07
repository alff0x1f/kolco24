"""Resource version fingerprints for the mobile sync manifest + ETags.

``teams_version`` is the **single source of truth** for the teams resource
version: ``TeamsView`` wraps it in quotes for the strong ``ETag`` header, and
``SyncView`` emits the bare value in the manifest's ``versions.teams``. Keep the
two consumers reading from this one helper so an ETag and a manifest probe can
never disagree.
"""

import hashlib

from django.db.models import Count, Max

from website.models.models import Athlet, Team


def teams_version(race_id):
    """Return a short, stable fingerprint of a race's teams + members.

    Combines ``MAX(updated_at)`` and the team count across the race's teams
    (``TeamManager`` already excludes ``is_deleted``) with ``MAX(updated_at)``
    over their members, so a team edit, a member rename, or a team add/remove
    all move the fingerprint. Teams with ``category2=None`` are out of scope
    (a race owns teams via ``category2.race``) and excluded by the filter.

    None aggregates (empty race) render as the literal ``"None"`` → stable,
    non-crashing. Returns **bare** hex (no quotes).
    """
    teams = Team.objects.filter(category2__race_id=race_id).aggregate(
        max_updated=Max("updated_at"),
        count=Count("id"),
    )
    members = Athlet.objects.filter(team__category2__race_id=race_id).aggregate(
        max_updated=Max("updated_at"),
    )
    raw = f"{teams['max_updated']}|{members['max_updated']}|{teams['count']}"
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()
