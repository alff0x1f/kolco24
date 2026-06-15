"""Backfill / repair the stored legend-encryption blobs.

Bulk path for the :mod:`apps.mobile.legend_crypto` service layer — the signals
(Task 4) keep the blobs consistent on individual edits, but a migration backfill
or a global repair needs to (re-)seal every locked КП and rebuild every tag's
bundle in one pass, which this command does by calling the service functions
directly (no signals involved).

Order matters: КП are sealed **first** so every ``content_key`` exists before the
bundles that reference it are built.

Usage::

    manage.py rebuild_legend_crypto [--race <id>] [--regenerate-codes]

``--race`` scopes the work to a single race; ``--regenerate-codes`` clears each
tag's ``code`` first so a fresh one is minted (re-provisioning — this invalidates
any code already written into a physical NFC tag).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.mobile.legend_crypto import build_bundle, seal_checkpoint
from website.models.checkpoint import Checkpoint, CheckpointTag


class Command(BaseCommand):
    help = "Re-seal all locked checkpoints and rebuild all tag bundles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--race",
            type=int,
            default=None,
            help="Restrict to a single race id (default: all races).",
        )
        parser.add_argument(
            "--regenerate-codes",
            action="store_true",
            help="Mint a fresh code for every tag (invalidates written tags).",
        )

    def handle(self, *args, **options):
        race_id = options["race"]
        regenerate = options["regenerate_codes"]

        checkpoints = Checkpoint.objects.all()
        tags = CheckpointTag.objects.select_related("point").all()
        if race_id is not None:
            checkpoints = checkpoints.filter(race_id=race_id)
            tags = tags.filter(point__race_id=race_id)

        sealed = 0
        for cp in checkpoints:
            seal_checkpoint(cp)
            if cp.is_legend_locked:
                sealed += 1

        built = 0
        for tag in tags:
            if regenerate:
                tag.code = None
            build_bundle(tag)
            built += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Sealed {sealed} locked checkpoint(s); rebuilt {built} bundle(s)."
            )
        )
