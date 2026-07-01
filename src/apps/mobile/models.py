from django.conf import settings
from django.db import models
from django.utils import timezone


class AppInstall(models.Model):
    """Per-install usage stats for the mobile app.

    One row per app-generated ``install_id`` (a UUID created on first launch and
    stored locally by the client). Updated best-effort on each verified request.
    """

    install_id = models.CharField(max_length=64, unique=True)
    platform = models.CharField(max_length=16, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    key_id = models.CharField(max_length=32, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    request_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.install_id} ({self.platform})"


class AppAuthFailure(models.Model):
    """Aggregated record of failed ``/app/*`` auth attempts.

    One row per distinct ``(ip, key_id, reason)`` (not per attempt) to bound
    table growth — a brute-force run is thousands of requests. Written
    best-effort from ``AppAPIView.permission_denied``; the permission itself
    does no DB writes. ``key_id`` is the *claimed* one and may be spoofed.
    """

    ip = models.GenericIPAddressField()
    key_id = models.CharField(max_length=32, blank=True)  # claimed, may be spoofed
    reason = models.CharField(max_length=32)
    count = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    last_path = models.CharField(max_length=255, blank=True)
    last_install_id = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = ("ip", "key_id", "reason")

    def __str__(self):
        return f"{self.ip} {self.key_id} {self.reason} x{self.count}"


class MobileToken(models.Model):
    """Revocable opaque bearer token for a per-person mobile login.

    Layered on top of the per-build HMAC (``SignedAppPermission``): a build
    proves it is one of ours, and this token proves which *person* is acting.
    General-purpose — any authenticated user gets one; admin capability is
    decided per-action (``can_edit_race``), not by this row.

    Only the sha256 hex of the high-entropy raw token is stored
    (``token_hash``); the raw token is returned to the client exactly once at
    login. Token hashing is *not* password hashing — a 256-bit random token is
    not brute-forceable, so a fast unsalted sha256 is correct and lets the
    lookup hit the indexed column. Not an ETag-fingerprinted model, so there is
    no ``updated_at`` discipline here.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mobile_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f"MobileToken(user={self.user_id}, active={self.is_active})"


class TrackPoint(models.Model):
    """One GPS fix uploaded by the Android app during a race.

    The primary key is the **client-generated UUID** (``id``): the idempotency
    key *is* the PK, so a re-sent point hits a PK conflict and is silently
    skipped by ``bulk_create(..., ignore_conflicts=True)`` — no secondary unique
    index, no upsert loop.

    Rows are **write-only / immutable**: ingestion never reads or updates a
    ``TrackPoint``. There is therefore **no ``updated_at``** and the model is
    deliberately **not** in ``versioning.py`` — it never touches the
    fingerprint/``sync`` ETag machinery.

    ``install_id`` (the device, ``X-Install-Id``) groups a phone's many
    recording sessions; ``segment_id`` (a random UUID per session) distinguishes
    those sessions — together they separate a team recording from two phones.
    """

    id = models.CharField(max_length=64, primary_key=True)
    team = models.ForeignKey(
        "website.Team",
        on_delete=models.CASCADE,
        related_name="track_points",
    )
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        related_name="track_points",
    )
    install_id = models.CharField(max_length=64)
    segment_id = models.CharField(max_length=64)
    lat = models.FloatField()
    lon = models.FloatField()
    accuracy = models.FloatField()
    altitude = models.FloatField(null=True)
    vertical_accuracy = models.FloatField(null=True)
    gps_time_ms = models.BigIntegerField()
    trusted_ms = models.BigIntegerField(null=True)
    elapsed_at = models.BigIntegerField()
    boot_count = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"TrackPoint({self.id} team={self.team_id} race={self.race_id})"


class Mark(models.Model):
    """One checkpoint-take report (взятие КП) uploaded by the Android app.

    The primary key is the **client-generated UUID** (``id``): the idempotency
    key *is* the PK. Unlike :class:`TrackPoint` (strictly immutable), a ``Mark``
    is **enrichment-upserted** on a repeat ``id`` — the client deliberately
    re-sends the same ``id`` when a late GPS fix (``location``) or a new roster
    member (``present``) arrives after the DTO was serialized but before the row
    was marked uploaded. Enrichment is monotonic (location null→value, ``present``
    only grows, ``complete`` false→true; take-moment times and ``cp_*`` are
    stable), so a blind last-write-wins upsert of the scalars is correct.

    Mechanism (see the view): ``Mark.objects.bulk_create(objs,
    update_conflicts=True, unique_fields=["id"], update_fields=MARK_UPDATE_FIELDS)``
    — Postgres ``ON CONFLICT (id) DO UPDATE``, one atomic statement.

    There is **no ``updated_at``** and the model is deliberately **not** in
    ``versioning.py`` — nothing reads a version off ``Mark``, so there is no
    fingerprint to keep fresh. ``created_at`` (``auto_now_add``) is excluded from
    ``update_fields`` so an enrichment upsert preserves the original insert time.

    ``verified`` is server-computed at ingest: ``sha256(bytes.fromhex(cp_code))
    [:16]`` must match a ``CheckpointTag.bid`` of the claimed ``checkpoint_id`` —
    proof the КП was physically scanned. Unknown КП / mismatch / bad hex →
    ``verified=False``, **row still stored**.

    ``checkpoint_id`` is a **plain int, not an FK** — an unknown КП is still
    accepted (data for later reattribution is never lost).
    """

    id = models.CharField(max_length=64, primary_key=True)
    team = models.ForeignKey(
        "website.Team",
        on_delete=models.CASCADE,
        related_name="marks",
    )
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        related_name="marks",
    )
    source_install_id = models.CharField(max_length=64)
    checkpoint_id = models.IntegerField()
    method = models.CharField(max_length=16)
    cp_code = models.CharField(max_length=64, blank=True)
    cp_nfc_uid = models.CharField(max_length=255, blank=True)
    expected_count = models.IntegerField()
    complete = models.BooleanField()
    verified = models.BooleanField()
    trusted_ms = models.BigIntegerField(null=True)
    wall_ms = models.BigIntegerField()
    elapsed_at = models.BigIntegerField(null=True)
    boot_count = models.IntegerField(null=True)
    loc_lat = models.FloatField(null=True)
    loc_lon = models.FloatField(null=True)
    loc_accuracy = models.FloatField(null=True)
    loc_altitude = models.FloatField(null=True)
    loc_vertical_accuracy = models.FloatField(null=True)
    loc_gps_time_ms = models.BigIntegerField(null=True)
    loc_elapsed_at = models.BigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Mark({self.id} team={self.team_id} cp={self.checkpoint_id})"


# Every scalar field except the PK (``id``) and the insert-time (``created_at``).
# Passed to ``bulk_create(update_conflicts=True, update_fields=MARK_UPDATE_FIELDS)``
# so a repeat ``id`` last-write-wins-merges the enriched payload while preserving
# the original ``created_at``.
MARK_UPDATE_FIELDS = [
    "team",
    "race",
    "source_install_id",
    "checkpoint_id",
    "method",
    "cp_code",
    "cp_nfc_uid",
    "expected_count",
    "complete",
    "verified",
    "trusted_ms",
    "wall_ms",
    "elapsed_at",
    "boot_count",
    "loc_lat",
    "loc_lon",
    "loc_accuracy",
    "loc_altitude",
    "loc_vertical_accuracy",
    "loc_gps_time_ms",
    "loc_elapsed_at",
]


class MarkPresent(models.Model):
    """One present team member at the moment a :class:`Mark` was recorded.

    A normalized child of ``Mark`` (chosen over a single JSON column so the
    roster is queryable for the future scoring/reattribution task). Identity is
    the **physical chip** (``nfc_uid``/``code``) — the server resolves
    ``uid/code → member`` against its own pool, not trusting the client
    ``number``. ``unique_together("mark", "number_in_team")`` makes the child
    insert conflict-safe on a re-send: ``bulk_create(ignore_conflicts=True)`` is
    additive — missing slots inserted, already-stored slots skipped.

    A ``{nfc_uid: null, number: 0}`` row is the **sentinel** "no snapshot" — a
    counted-but-unsnapshotted member, not a real empty UID.
    """

    mark = models.ForeignKey(
        Mark,
        on_delete=models.CASCADE,
        related_name="present",
    )
    nfc_uid = models.CharField(max_length=255, null=True)
    code = models.CharField(max_length=64, null=True)
    number = models.IntegerField()
    number_in_team = models.IntegerField()

    class Meta:
        unique_together = ("mark", "number_in_team")

    def __str__(self):
        return f"MarkPresent(mark={self.mark_id} n={self.number_in_team})"


def _mark_photo_path(instance, filename):
    """Deterministic, unguessable path: two client UUIDs. ``filename`` is ignored."""
    return f"mark_photos/{instance.mark_id}/{instance.frame_id}.jpg"


class MarkPhoto(models.Model):
    """One JPEG frame uploaded for a ``method="photo"`` :class:`Mark`.

    The primary key is the default auto id; idempotency is enforced by
    ``unique_together("mark", "frame_id")`` — a client-resent frame is detected
    by the view before insert, and a concurrent duplicate is caught via
    ``IntegrityError`` on this constraint.

    Rows are **write-only / immutable** like :class:`TrackPoint`: there is no
    ``updated_at`` and the model is deliberately **not** in ``versioning.py`` —
    nothing reads a version off it.
    """

    mark = models.ForeignKey(Mark, on_delete=models.CASCADE, related_name="photos")
    frame_id = models.CharField(max_length=64)
    image = models.FileField(upload_to=_mark_photo_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("mark", "frame_id")

    def __str__(self):
        return f"MarkPhoto(mark={self.mark_id} frame={self.frame_id})"
