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
