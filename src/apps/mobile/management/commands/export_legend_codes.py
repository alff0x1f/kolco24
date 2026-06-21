"""Print the per-tag NFC codes for writing into physical tags.

Operational tooling: emits one ``nfc_uid / КП number / code(hex)`` line per tag
of a race so the field crew can write each ``code`` into the matching NFC tag's
user memory (the mobile app reads it back to compute ``bid`` and unlock the
legend offline). Tags without a code yet print ``code = —`` — run
``rebuild_legend_crypto`` first to mint them.

Usage::

    manage.py export_legend_codes --race <id>
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from website.models.checkpoint import CheckpointTag


class Command(BaseCommand):
    help = "Print nfc_uid / КП number / code(hex) for every tag of a race."

    def add_arguments(self, parser):
        parser.add_argument(
            "--race",
            type=int,
            required=True,
            help="Race id whose tags to export.",
        )

    def handle(self, *args, **options):
        race_id = options["race"]
        tags = (
            CheckpointTag.objects.filter(checkpoint__race_id=race_id)
            .select_related("checkpoint")
            .order_by("checkpoint__number", "id")
        )
        for tag in tags:
            code_hex = bytes(tag.code).hex() if tag.code else "—"
            self.stdout.write(f"{tag.nfc_uid}\t{tag.checkpoint.number}\t{code_hex}")
