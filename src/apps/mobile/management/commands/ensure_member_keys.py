"""Mint missing bracelet keys (``Race.member_key``) for published races.

Run on the cloud server before copying the DB to the race LAN server — the LAN
server never mints a key itself.

Usage::

    manage.py ensure_member_keys [--race <id>]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from website.models.race import Race

from ...member_key import ensure_member_key


class Command(BaseCommand):
    help = "Mint missing K24 v2 bracelet keys for published races."

    def add_arguments(self, parser):
        parser.add_argument("--race", type=int, help="Only this race id.")

    def handle(self, *args, **options):
        races = Race.objects.filter(is_published=True)
        if options["race"] is not None:
            races = races.filter(pk=options["race"])
        minted = 0
        for race_id in races.filter(member_key__isnull=True).values_list(
            "pk", flat=True
        ):
            ensure_member_key(race_id)
            minted += 1
            self.stdout.write(f"race {race_id}: key minted")
        self.stdout.write(f"done, minted {minted}")
