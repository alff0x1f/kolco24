"""Build gallery thumbnails for photo-contest entries that have none.

New uploads get a thumbnail in ``create_entry``; this backfills older entries.
Thumbnails are made from the stored preview (the original is private and much
larger), the same way ``create_entry`` makes them.

Usage::

    manage.py build_photo_thumbs [--force]

``--force`` rebuilds every thumbnail, e.g. after changing its size.
"""

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from apps.photo_contest import images
from apps.photo_contest.models import PhotoEntry


class Command(BaseCommand):
    help = "Build missing photo-contest thumbnails from the stored previews."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="Rebuild existing thumbnails too."
        )

    def handle(self, *args, force=False, **options):
        entries = PhotoEntry.objects.exclude(preview="").order_by("pk")
        if not force:
            entries = entries.filter(thumb="")
        built = failed = 0
        for entry in entries.iterator():
            try:
                with entry.preview.open("rb") as preview:
                    data = images.make_preview(
                        preview,
                        max_side=images.THUMB_MAX_SIDE,
                        quality=images.THUMB_QUALITY,
                    )
            except (OSError, images.ImageRejected) as exc:
                failed += 1
                self.stderr.write(f"Фото #{entry.pk}: {exc}")
                continue
            # The old file goes only once the new bytes exist, and before the
            # save, so the new one takes its name instead of a suffixed copy.
            if entry.thumb:
                entry.thumb.delete(save=False)
            name = entry.preview.name.removesuffix(".jpg") + "_thumb.jpg"
            entry.thumb.save(name, ContentFile(data), save=False)
            entry.save(update_fields=["thumb", "thumb_width", "thumb_height"])
            built += 1
        self.stdout.write(f"Миниатюр создано: {built}, ошибок: {failed}.")
