"""Serializers for the mobile-app endpoints."""

import logging
import math

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


class TagCreateSerializer(serializers.Serializer):
    """Validate the ``POST /app/race/<race_id>/tags/`` body.

    ``checkpoint_id`` is the **checkpoint id** (``Checkpoint.number`` is not
    unique per race — see the plan's "КП identity" decision), ``nfc_uid`` is the
    scanned chip UID. Both required; a blank ``nfc_uid`` is rejected here (400)
    before it reaches the model's ``save()``, which raises ``ValueError`` on
    blank (→ 500).
    """

    checkpoint_id = serializers.IntegerField()
    # max_length mirrors CheckpointTag.nfc_uid (255). Without it an oversized UID
    # reaches the INSERT and PostgreSQL raises → 500 instead of a clean 400.
    nfc_uid = serializers.CharField(
        allow_blank=False, trim_whitespace=True, max_length=255
    )


class FiniteFloatField(serializers.FloatField):
    """FloatField that rejects NaN and infinity.

    DRF's FloatField converts strings like "NaN" / "1e309" to nan/inf via
    Python's float() — these pass min/max validators because NaN comparisons
    always return False and inf only fails when an explicit max_value is set.
    This subclass adds a math.isfinite() check after the standard conversion.
    """

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            self.fail("invalid")
        return value


class TrackPointSerializer(serializers.Serializer):
    """Validate one GPS fix in a ``POST /app/race/<race_id>/track/`` batch.

    ``id``/``segment_id`` are **opaque client strings** (the Kotlin DTO type is
    ``String``, not ``UUID`` — production mints UUIDs but the server must not
    police the client id format; ``id`` is just the PK). ``min_length=1`` only
    guards against an empty PK.

    GPS magnitudes are physically non-negative (meters / epoch-ms / monotonic-ms
    / boot counter), so they carry ``min_value=0`` — an out-of-range point is a
    client bug that surfaces as a 400. ``altitude`` is the one exception: it may
    be negative (below sea level), so it is unbounded.
    """

    id = serializers.CharField(max_length=64, min_length=1)
    segment_id = serializers.CharField(max_length=64, min_length=1)
    lat = FiniteFloatField(min_value=-90, max_value=90)
    lon = FiniteFloatField(min_value=-180, max_value=180)
    accuracy = FiniteFloatField(min_value=0)
    altitude = FiniteFloatField(required=False, allow_null=True)
    vertical_accuracy = FiniteFloatField(required=False, allow_null=True, min_value=0)
    # max_value mirrors BigIntegerField cap (2^63 − 1); without it a malicious
    # client can send an arbitrarily-large int that passes DRF validation but
    # causes a DataError at bulk_create → 500 instead of a clean 400.
    gps_time_ms = serializers.IntegerField(min_value=0, max_value=9223372036854775807)
    trusted_ms = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=9223372036854775807
    )
    elapsed_at = serializers.IntegerField(min_value=0, max_value=9223372036854775807)
    boot_count = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=2147483647
    )


class TrackUploadSerializer(serializers.Serializer):
    """Validate the ``POST /app/race/<race_id>/track/`` body.

    ``team_id`` identifies the team the points belong to (the view checks it is
    in the race). ``points`` is a batch of up to 500 fixes (the app's batch cap)
    — an oversized batch is a 400 (all-or-nothing), bounding memory + the single
    ``bulk_create`` insert. An empty list is valid and acks ``[]``.
    """

    team_id = serializers.IntegerField(min_value=1, max_value=2147483647)
    points = TrackPointSerializer(many=True, allow_empty=True, max_length=500)


class MarkLocationSerializer(serializers.Serializer):
    """Validate the nested ``location`` of one mark (anti-cheat coordinate).

    The whole object may be ``null`` (no fix yet — see ``MarkSerializer.location``)
    so every field is optional/nullable here. ``lat``/``lon`` are bounded like the
    track fixes; ``accuracy``/``vertical_accuracy`` are physically non-negative;
    ``altitude`` is unbounded (below sea level is valid). ``gps_time_ms``/
    ``elapsed_at`` are BigInt columns (``2^63 − 1`` cap) — note these are the
    *fix's* times, deliberately namespaced under ``location`` so they don't
    collide with the take-moment times on the mark itself.
    """

    lat = FiniteFloatField(required=False, allow_null=True, min_value=-90, max_value=90)
    lon = FiniteFloatField(
        required=False, allow_null=True, min_value=-180, max_value=180
    )
    accuracy = FiniteFloatField(required=False, allow_null=True, min_value=0)
    altitude = FiniteFloatField(required=False, allow_null=True)
    vertical_accuracy = FiniteFloatField(required=False, allow_null=True, min_value=0)
    gps_time_ms = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=9223372036854775807
    )
    elapsed_at = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=9223372036854775807
    )


class PresentMemberSerializer(serializers.Serializer):
    """Validate one present-member snapshot inside a mark's ``present[]``.

    Identity is the physical chip (``nfc_uid``/``code``); a
    ``{nfc_uid: null, number: 0}`` row is the sentinel "no snapshot". ``number``/
    ``number_in_team`` back 32-bit ``IntegerField`` columns, so they carry the
    ``2^31 − 1`` cap (not the BigInt one) — an oversized int is a clean 400.
    """

    nfc_uid = serializers.CharField(max_length=255, allow_null=True, required=False)
    code = serializers.CharField(max_length=64, allow_null=True, required=False)
    number = serializers.IntegerField(min_value=0, max_value=2147483647)
    number_in_team = serializers.IntegerField(min_value=0, max_value=2147483647)


class MarkSerializer(serializers.Serializer):
    """Validate one checkpoint-take in a ``POST /app/race/<race_id>/marks/`` batch.

    ``id`` is the opaque client UUID (the idempotency key / PK; ``min_length=1``
    only guards an empty PK). ``method`` is a ``ChoiceField`` — only the two
    contract values ``nfc``/``photo`` are accepted, any other is a 400 by
    decision. ``cp_code``/``cp_nfc_uid`` are ``allow_blank`` (a future ``photo``
    mark carries no scanned code). ``wall_ms`` is **required** (the sole fallback
    when ``trusted_ms`` is null); ``trusted_ms``/``elapsed_at`` are nullable BigInt
    (``2^63 − 1`` cap); ``boot_count`` backs a **32-bit** column, so the ``2^31 − 1``
    cap (crossing the two up still 500s). ``location`` is ``required=False`` —
    load-bearing: the kotlinx client serializes with ``encodeDefaults=false`` and
    ``MarkDto.location`` defaults to ``null``, so a fix-less take **omits** the key
    rather than sending ``location: null``.
    """

    id = serializers.CharField(min_length=1, max_length=64)
    checkpoint_id = serializers.IntegerField(min_value=0, max_value=2147483647)
    method = serializers.ChoiceField(choices=["nfc", "photo"])
    cp_code = serializers.CharField(max_length=64, allow_blank=True)
    cp_nfc_uid = serializers.CharField(max_length=255, allow_blank=True)
    expected_count = serializers.IntegerField(min_value=0, max_value=2147483647)
    complete = serializers.BooleanField()
    trusted_ms = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=9223372036854775807
    )
    wall_ms = serializers.IntegerField(min_value=0, max_value=9223372036854775807)
    elapsed_at = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=9223372036854775807
    )
    boot_count = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=2147483647
    )
    present = PresentMemberSerializer(many=True, allow_empty=True, max_length=100)
    location = MarkLocationSerializer(required=False, allow_null=True)


class MarkUploadSerializer(serializers.Serializer):
    """Validate the ``POST /app/race/<race_id>/marks/`` body.

    ``team_id`` identifies the team the marks belong to (the view checks it is in
    the race). ``source_install_id`` is the provenance grouping key, read from the
    **signed body** (not the ``X-Install-Id`` header). ``marks`` is a batch of up
    to 500 takes (all-or-nothing — an oversized batch is a 400); an empty list is
    valid and acks ``[]``.
    """

    team_id = serializers.IntegerField(min_value=1, max_value=2147483647)
    source_install_id = serializers.CharField(max_length=64)
    marks = MarkSerializer(many=True, allow_empty=True, max_length=500)


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

    - **identity** — ``bid → checkpoint_id`` (1:1, **always** present, incl. open
      КП): the app computes ``bid = sha256(scanned_code).hexdigest()[:16]`` from
      the code in the tag's NFC user memory and looks it up here to resolve which
      checkpoint (``checkpoint_id`` = ``CheckpointTag.checkpoint_id``) was
      physically scanned, fully offline.
    - **unlock** — ``iv``/``ct`` (locked КП **only**): flattened from
      ``CheckpointTag.bundle_blob``. When present, HKDF-decrypts to
      ``{cp_id: content_key}`` and then decrypts each locked КП's ``enc`` blob.
      ``None`` for an open tag (identity-only, nothing to decrypt).

    The raw ``code``/``nfc_uid`` never travel on the wire.
    """

    bid = serializers.CharField()
    checkpoint_id = serializers.IntegerField()
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
    into a label and build a category filter, plus the category's control time
    (``control_time``, minutes; ``0`` = не задано) and per-minute overtime
    penalty (``overtime_penalty``, баллы; ``0`` = без штрафа) — no ``is_active``
    (the list intentionally includes inactive categories so a team's id still
    resolves), no ``description`` or size fields (YAGNI).
    """

    class Meta:
        model = Category
        fields = [
            "id",
            "code",
            "short_name",
            "name",
            "order",
            "control_time",
            "overtime_penalty",
        ]


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
