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
