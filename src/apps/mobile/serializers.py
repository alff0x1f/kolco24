"""Serializers for the mobile-app endpoints."""

from rest_framework import serializers

from website.models.checkpoint import Checkpoint


class LegendCheckpointSerializer(serializers.ModelSerializer):
    """Public legend view of a checkpoint — never exposes ``id``/``iterator``/``year``."""

    class Meta:
        model = Checkpoint
        fields = ["number", "cost", "type", "description"]
