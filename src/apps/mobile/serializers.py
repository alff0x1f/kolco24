"""Serializers for the mobile-app endpoints."""

from rest_framework import serializers

from website.models.checkpoint import Checkpoint
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
