"""Serializers for the mobile-app endpoints."""

from rest_framework import serializers

from website.models.checkpoint import Checkpoint
from website.models.models import Athlet, Team
from website.models.race import Race


class RaceListSerializer(serializers.ModelSerializer):
    """Public list view of a published race (no images)."""

    class Meta:
        model = Race
        fields = [
            "id",
            "name",
            "slug",
            "date",
            "date_end",
            "place",
            "reg_status",
            "is_legend_visible",
        ]


class LegendCheckpointSerializer(serializers.ModelSerializer):
    """Public legend view of a checkpoint.

    Never exposes ``id``/``iterator``/``year``.
    """

    class Meta:
        model = Checkpoint
        fields = ["number", "cost", "type", "description"]


class MemberSerializer(serializers.ModelSerializer):
    """Nested member (``Athlet``) composition of a team."""

    class Meta:
        model = Athlet
        fields = ["name", "birth", "number_in_team"]


class TeamSerializer(serializers.ModelSerializer):
    """Mobile view of a team with its nested member composition.

    ``members`` prefers the prefetched ``athlet_set`` (new storage); if that
    set is empty it falls back to the legacy ``athlet1..6`` / ``birth1..6``
    columns that the existing web UI still writes to.  The fallback keeps
    member data visible until every team is stored in the ``Athlet`` table.
    """

    category2 = serializers.IntegerField(source="category2_id")
    members = serializers.SerializerMethodField()

    def get_members(self, team):
        # Prefer Athlet rows (ordering comes from the view's Prefetch).
        athlets = list(team.athlet_set.all())
        if athlets:
            return MemberSerializer(athlets, many=True).data
        # Fall back to legacy athlet1..6 / birth1..6 columns.
        # Cap at ucount so slots above the active roster (stale after a size
        # reduction — the web form hides but does not clear those inputs) are
        # not exposed.
        result = []
        for i in range(1, min(team.ucount, 6) + 1):
            name = getattr(team, f"athlet{i}", "")
            birth = getattr(team, f"birth{i}", 0)
            if name:
                result.append({"name": name, "birth": birth, "number_in_team": i})
        return result

    class Meta:
        model = Team
        fields = [
            "id",
            "teamname",
            "category2",
            "ucount",
            "paid_people",
            "start_time",
            "finish_time",
            "members",
        ]
