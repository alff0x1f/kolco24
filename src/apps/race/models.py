import datetime

from django.conf import settings
from django.db import models


class RaceExtra(models.Model):
    """Per-race catalogue of purchasable add-ons (maps, transfer, breakfast...)."""

    race = models.ForeignKey(
        "website.Race",
        related_name="extras",
        on_delete=models.CASCADE,
    )
    code = models.CharField(max_length=32)  # "map" | "transfer" | "breakfast"
    name = models.CharField(max_length=100)  # display, e.g. "Трансфер"
    price = models.IntegerField(default=0)  # ₽ per unit
    free_per_team = models.IntegerField(default=0)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("race", "code")
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} ({self.code}) — {self.price}₽"


class TeamExtra(models.Model):
    """Per-team desired vs paid counts for an add-on."""

    team = models.ForeignKey(
        "website.Team",
        related_name="extras",
        on_delete=models.CASCADE,
    )
    race_extra = models.ForeignKey(
        RaceExtra,
        related_name="team_extras",
        on_delete=models.PROTECT,
    )
    count = models.IntegerField(default=0)
    count_paid = models.IntegerField(default=0)

    class Meta:
        unique_together = ("team", "race_extra")

    def __str__(self):
        return (
            f"{self.team_id} × {self.race_extra.code}: {self.count}/{self.count_paid}"
        )


class PaymentExtra(models.Model):
    """Per-payment snapshot of the add-on delta a payment covers."""

    payment = models.ForeignKey(
        "website.Payment",
        related_name="extras",
        on_delete=models.CASCADE,
    )
    race_extra = models.ForeignKey(
        RaceExtra,
        related_name="payment_extras",
        on_delete=models.PROTECT,
    )
    count = models.IntegerField(default=0)
    unit_price = models.IntegerField(default=0)  # price snapshot at charge time

    def __str__(self):
        return f"payment {self.payment_id}: {self.race_extra.code} ×{self.count}"


class Protocol(models.Model):
    """A snapshot version of a race's results protocol (draft or frozen final)."""

    DRAFT = "draft"
    FINAL = "final"
    STATUS_CHOICES = [(DRAFT, "draft"), (FINAL, "final")]

    race = models.ForeignKey(
        "website.Race",
        related_name="protocols",
        on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Protocol #{self.id} ({self.status}) for race {self.race_id}"


class ProtocolRow(models.Model):
    """A denormalized snapshot of one team's result within a Protocol."""

    protocol = models.ForeignKey(
        Protocol,
        related_name="rows",
        on_delete=models.CASCADE,
    )

    # Identification
    place = models.IntegerField(default=0)
    team_id = models.IntegerField()
    start_number = models.CharField(max_length=50, blank=True, default="")
    team_name = models.CharField(max_length=200, blank=True, default="")
    members = models.TextField(blank=True, default="")
    club = models.CharField(max_length=200, blank=True, default="")
    city = models.CharField(max_length=200, blank=True, default="")
    member_count = models.IntegerField(default=0)

    # Category (copied as of snapshot time)
    category_id = models.IntegerField()
    category_code = models.CharField(max_length=15, blank=True, default="")
    category_name = models.CharField(max_length=50, blank=True, default="")
    category_short_name = models.CharField(max_length=15, blank=True, default="")

    # NFC
    nfc_checkpoints = models.TextField(blank=True, default="")
    nfc_count = models.IntegerField(default=0)
    nfc_score = models.IntegerField(default=0)

    # Photo
    photo_checkpoints = models.TextField(blank=True, default="")
    photo_count = models.IntegerField(default=0)
    photo_score = models.IntegerField(default=0)

    # Score totals
    chips_count = models.IntegerField(default=0)
    total_score = models.IntegerField(default=0)

    # Time
    start_time_ms = models.BigIntegerField(default=0)
    finish_time_ms = models.BigIntegerField(default=0)
    duration_ms = models.BigIntegerField(default=0)
    duration_str = models.CharField(max_length=20, blank=True, default="")

    # Result
    penalty = models.IntegerField(default=0)
    final_score = models.IntegerField(default=0)
    dnf = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["protocol", "category_id"]),
        ]

    def __str__(self):
        return f"ProtocolRow #{self.id}: {self.team_name} ({self.place})"

    @property
    def start_time_date(self):
        if not self.start_time_ms:
            return None
        return datetime.datetime.fromtimestamp(self.start_time_ms / 1000)

    @property
    def finish_time_date(self):
        if not self.finish_time_ms:
            return None
        return datetime.datetime.fromtimestamp(self.finish_time_ms / 1000)
