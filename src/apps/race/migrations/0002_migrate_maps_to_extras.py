"""Backfill the generic add-on models from the legacy maps columns.

Legacy maps live on three places:
- ``Team.map_count`` / ``Team.map_count_paid`` (desired vs paid),
- ``Payment.map`` (per-payment delta snapshot).

This migration creates a ``code="map"`` :class:`RaceExtra` per race that has any
maps usage, then mirrors the per-team counts into :class:`TeamExtra` and the
per-payment deltas into :class:`PaymentExtra`. The legacy columns are left in
place (readable) — they are dropped in a deferred follow-up migration after
production verification.
"""

from django.db import migrations
from django.db.models import Q

MAP_NAME = "Доп. карты"
MAP_PRICE = 200
MAP_FREE_PER_TEAM = 2


def _map_extra_for_race(RaceExtra, race):
    """get_or_create the race's ``code="map"`` extra (defaults-only on collide)."""
    extra, _ = RaceExtra.objects.get_or_create(
        race=race,
        code="map",
        defaults={
            "name": MAP_NAME,
            "price": MAP_PRICE,
            "free_per_team": MAP_FREE_PER_TEAM,
            "order": 0,
            "is_active": True,
        },
    )
    return extra


def forward(apps, schema_editor):
    Team = apps.get_model("website", "Team")
    Payment = apps.get_model("website", "Payment")
    RaceExtra = apps.get_model("race_app", "RaceExtra")
    TeamExtra = apps.get_model("race_app", "TeamExtra")
    PaymentExtra = apps.get_model("race_app", "PaymentExtra")

    # Teams → TeamExtra (and the race's map RaceExtra, lazily created).
    teams = Team.objects.filter(is_deleted=False).filter(
        Q(map_count__gt=0) | Q(map_count_paid__gt=0)
    )
    for team in teams:
        category = getattr(team, "category2", None)
        race = getattr(category, "race", None) if category else None
        if race is None:
            print(f"[migrate maps] skipping team {team.id}: no category2/race")
            continue
        extra = _map_extra_for_race(RaceExtra, race)
        TeamExtra.objects.create(
            team=team,
            race_extra=extra,
            count=team.map_count,
            count_paid=team.map_count_paid,
        )

    # Payments → PaymentExtra.
    for payment in Payment.objects.filter(map__gt=0):
        team = payment.team
        category = getattr(team, "category2", None) if team else None
        race = getattr(category, "race", None) if category else None
        if race is None:
            print(f"[migrate maps] skipping payment {payment.id}: no team/category2")
            continue
        extra = _map_extra_for_race(RaceExtra, race)
        PaymentExtra.objects.create(
            payment=payment,
            race_extra=extra,
            count=payment.map,
            unit_price=MAP_PRICE,
        )


def reverse(apps, schema_editor):
    RaceExtra = apps.get_model("race_app", "RaceExtra")
    TeamExtra = apps.get_model("race_app", "TeamExtra")
    PaymentExtra = apps.get_model("race_app", "PaymentExtra")

    PaymentExtra.objects.filter(race_extra__code="map").delete()
    TeamExtra.objects.filter(race_extra__code="map").delete()
    RaceExtra.objects.filter(code="map").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("race_app", "0001_initial"),
        ("website", "0072_payment_vtb_payment"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
