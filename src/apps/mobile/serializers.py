"""Serializers for the mobile-app endpoints."""

import logging

from rest_framework import serializers

from website.models.checkpoint import CheckpointSecret
from website.models.models import Athlet, Team
from website.models.race import Category, Race

logger = logging.getLogger(__name__)


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


class BundleSerializer(serializers.Serializer):
    """Per-tag envelope bundle for the offline legend unlock.

    Flattens ``CheckpointTag.bundle_blob``'s ``{"iv", "ct"}`` next to the tag's
    ``bid`` and ``check_method``. The app finds a bundle by
    ``bid = sha256(scanned_code).hexdigest()[:16]``, HKDF-decrypts it to
    ``{cp_id: content_key}``, then decrypts each locked КП's ``enc`` blob — all
    offline. The raw ``code``/``nfc_uid`` never travel on the wire.
    """

    bid = serializers.CharField()
    iv = serializers.SerializerMethodField()
    ct = serializers.SerializerMethodField()
    check_method = serializers.CharField()

    def get_iv(self, tag):
        return (tag.bundle_blob or {}).get("iv")

    def get_ct(self, tag):
        return (tag.bundle_blob or {}).get("ct")


class LegendCheckpointSerializer(serializers.Serializer):
    """Public legend view of a checkpoint.

    Branches on ``is_legend_locked``: a **locked** КП exposes only
    ``{id, number, type, enc}`` (the precomputed ``secret.enc_blob`` ciphertext),
    so its ``cost``/``description`` never leave the server in cleartext; an
    **open** КП exposes ``{id, number, type, cost, description}``. Never exposes
    ``iterator``/``year``. The view must ``select_related("secret")`` so the
    locked branch reads the prefetched secret without an extra query.
    """

    def to_representation(self, cp):
        data = {"id": cp.id, "number": cp.number, "type": cp.type}
        if cp.is_legend_locked:
            try:
                secret = cp.secret
            except CheckpointSecret.DoesNotExist:
                secret = None
            if secret is None:
                logger.error(
                    "Locked checkpoint %d has no CheckpointSecret; "
                    "run rebuild_legend_crypto to repair.",
                    cp.id,
                )
                # Fall back to cleartext so the mobile app does not receive
                # enc=null and crash; cleartext is already the open-КП contract.
                data["cost"] = cp.cost
                data["description"] = cp.description
                return data
            data["enc"] = secret.enc_blob
            return data
        data["cost"] = cp.cost
        data["description"] = cp.description
        return data


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
