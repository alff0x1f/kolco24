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

    ``members`` iterates the **prefetched** ``athlet_set`` directly (no
    ``.order_by`` here — that would re-query and defeat the view's
    ``Prefetch``); ordering is supplied by ``TeamsView``'s prefetch queryset.
    """

    category2 = serializers.IntegerField(source="category2_id")
    members = MemberSerializer(source="athlet_set", many=True, read_only=True)

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
