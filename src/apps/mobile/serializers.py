"""Serializers for the mobile-app endpoints."""

import logging

from rest_framework import serializers

from website.models.checkpoint import CheckpointSecret
from website.models.models import Athlet, Team
from website.models.race import Category, Race
from website.models.tag import Tag

logger = logging.getLogger(__name__)


class LoginSerializer(serializers.Serializer):
    """Validate the ``POST /app/login/`` body (``email`` + ``password``).

    Input-shape validation only — it never authenticates. A missing/blank field
    yields a 400 before the view touches ``authenticate``; bad credentials are a
    401 from the view (deliberately a different status from the 400, but with a
    generic, enumeration-safe message).
    """

    email = serializers.EmailField()
    password = serializers.CharField()


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
        ]


class TagSerializer(serializers.Serializer):
    """Per-tag identity (always) plus optional offline-unlock envelope.

    Two concerns, cleanly split:

    - **identity** — ``bid → point`` (1:1, **always** present, incl. open КП):
      the app computes ``bid = sha256(scanned_code).hexdigest()[:16]`` from the
      code in the tag's NFC user memory and looks it up here to resolve which
      checkpoint (``point`` = ``point_id``) was physically scanned, fully
      offline.
    - **unlock** — ``iv``/``ct`` (locked КП **only**): flattened from
      ``CheckpointTag.bundle_blob``. When present, HKDF-decrypts to
      ``{cp_id: content_key}`` and then decrypts each locked КП's ``enc`` blob.
      ``None`` for an open tag (identity-only, nothing to decrypt).

    The raw ``code``/``nfc_uid`` never travel on the wire.
    """

    bid = serializers.CharField()
    point = serializers.IntegerField(source="point_id")
    iv = serializers.SerializerMethodField()
    ct = serializers.SerializerMethodField()
    check_method = serializers.CharField()

    def get_iv(self, tag):
        return (tag.bundle_blob or {}).get("iv")

    def get_ct(self, tag):
        return (tag.bundle_blob or {}).get("ct")


class MemberTagSerializer(serializers.ModelSerializer):
    """Mobile view of a member tag (participant bracelet) — identity only.

    Exposes just ``number → nfc_uid`` so the app can resolve a bracelet scan
    offline. Deliberately omits ``id``/``last_seen_at`` (no internal id or scan
    timestamp leak). Distinct name from the legend ``TagSerializer`` above,
    which serializes ``CheckpointTag`` rows, not member ``Tag`` rows.
    """

    class Meta:
        model = Tag
        fields = ["number", "nfc_uid"]


class LegendCheckpointSerializer(serializers.Serializer):
    """Public legend view of a checkpoint.

    Branches on ``is_legend_locked``: a **locked** КП exposes only
    ``{id, number, type, color, enc}`` (the precomputed ``secret.enc_blob``
    ciphertext), so its ``cost``/``description`` never leave the server in
    cleartext; an **open** КП exposes ``{id, number, type, color, cost,
    description}``. ``color`` is a non-secret display token carried by **both**
    branches. Never exposes ``iterator``/``year``. The view must
    ``select_related("secret")`` so the locked branch reads the prefetched
    secret without an extra query.
    """

    def to_representation(self, cp):
        data = {"id": cp.id, "number": cp.number, "type": cp.type, "color": cp.color}
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
                # Fail closed: return only the identifier fields, no enc and no
                # cleartext. Leaking cleartext would defeat the encryption scheme;
                # the app treats a locked КП with no enc as undecryptable until
                # the admin runs rebuild_legend_crypto.
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

    The roster is then padded with empty-name slots up to
    ``min(floor(paid_people), ucount)`` so the app sees one slot per
    participant the team actually paid for and declared, even when not every
    name was entered (the web form leaves member-name fields optional). Names
    are never dropped: if real members exceed that floor (stale data after a
    size reduction), every named slot is still emitted. ``paid_people`` keeps
    unpaid/just-registered teams from sprouting synthetic slots. An empty slot
    is ``{"name": "", "number_in_team": i}`` — the app renders its own
    placeholder.
    """

    category2 = serializers.IntegerField(source="category2_id")
    members = serializers.SerializerMethodField()

    def get_members(self, team):
        # Collect real names keyed by slot. Prefer Athlet rows (ordering comes
        # from the view's Prefetch); fall back to legacy athlet1..6 columns.
        names = {}
        athlets = list(team.athlet_set.all())
        if athlets:
            for a in athlets:
                names[a.number_in_team] = a.name
        else:
            for i in range(1, 7):
                name = getattr(team, f"athlet{i}", "")
                if name:
                    names[i] = name

        # One slot per paid + declared participant; never drop a real member.
        target = min(int(team.paid_people), team.ucount)
        if names:
            target = max(target, max(names))

        return [
            {"name": names.get(i, ""), "number_in_team": i}
            for i in range(1, target + 1)
        ]

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
