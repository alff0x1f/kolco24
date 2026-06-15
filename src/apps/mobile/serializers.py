"""Serializers for the mobile-app endpoints."""

from rest_framework import serializers

from website.models.checkpoint import Checkpoint, CheckpointTag
from website.models.models import Athlet, Team
from website.models.race import Category, Race

from . import signing


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


class LegendTagSerializer(serializers.ModelSerializer):
    """Public legend view of an NFC tag.

    Never exposes the raw ``nfc_uid``: it is served only as ``tag_hash``, an
    HMAC of the raw UID keyed by the per-build secret (``context["secret"]``),
    so the physical tag IDs never travel on the wire. The app re-computes the
    same hash from a scanned UID to match scan → checkpoint offline.
    """

    tag_hash = serializers.SerializerMethodField()

    def get_tag_hash(self, tag):
        return signing.tag_hash(self.context["secret"], tag.nfc_uid)

    class Meta:
        model = CheckpointTag
        fields = ["id", "tag_hash", "check_method"]


class LegendCheckpointSerializer(serializers.ModelSerializer):
    """Public legend view of a checkpoint.

    Never exposes ``iterator``/``year``. ``tags`` carries hashed NFC tag UIDs
    (see ``LegendTagSerializer``); the resolved secret must be threaded through
    serializer ``context``.
    """

    tags = LegendTagSerializer(many=True)

    class Meta:
        model = Checkpoint
        fields = ["id", "number", "cost", "type", "description", "tags"]


class CategorySerializer(serializers.ModelSerializer):
    """Mobile view of a race category (display + filter only).

    Exposes just the fields the app needs to resolve a team's ``category2`` id
    into a label and build a category filter — no ``is_active`` (the list
    intentionally includes inactive categories so a team's id still resolves),
    no ``description`` or size fields (YAGNI).
    """

    class Meta:
        model = Category
        fields = ["id", "code", "short_name", "name", "order"]


class MemberSerializer(serializers.ModelSerializer):
    """Nested member (``Athlet``) composition of a team."""

    class Meta:
        model = Athlet
        fields = ["name", "number_in_team"]


class TeamSerializer(serializers.ModelSerializer):
    """Mobile view of a team with its nested member composition.

    ``members`` prefers the prefetched ``athlet_set`` (new storage); if that
    set is empty it falls back to the legacy ``athlet1..6`` columns that the
    existing web UI still writes to.  The fallback keeps
    member data visible until every team is stored in the ``Athlet`` table.
    """

    category2 = serializers.IntegerField(source="category2_id")
    members = serializers.SerializerMethodField()

    def get_members(self, team):
        # Prefer Athlet rows (ordering comes from the view's Prefetch).
        athlets = list(team.athlet_set.all())
        if athlets:
            return MemberSerializer(athlets, many=True).data
        # Fall back to legacy athlet1..6 columns.
        # Cap at ucount so slots above the active roster (stale after a size
        # reduction — the web form hides but does not clear those inputs) are
        # not exposed.
        result = []
        for i in range(1, min(team.ucount, 6) + 1):
            name = getattr(team, f"athlet{i}", "")
            if name:
                result.append({"name": name, "number_in_team": i})
        return result

    class Meta:
        model = Team
        fields = [
            "id",
            "teamname",
            "start_number",
            "category2",
            "ucount",
            "paid_people",
            "start_time",
            "finish_time",
            "members",
        ]
