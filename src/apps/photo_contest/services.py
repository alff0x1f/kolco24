from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction

from apps.photo_contest import images
from apps.photo_contest.models import PhotoEntry, PhotoNomination


class LimitReached(Exception):
    """The user already has the maximum number of photos in the nomination."""


def entry_dir(nomination):
    # One folder per contest, split by nomination: an archived contest is
    # backed up by copying one directory. Ids, not slugs — a path is fixed at
    # upload time and ids never change.
    return f"photo_contest/{nomination.contest_id}/{nomination.pk}"


def used_slots(nomination, user):
    return (
        PhotoEntry.objects.filter(nomination=nomination, uploaded_by=user)
        .exclude(status=PhotoEntry.REJECTED)
        .count()
    )


def create_entry(nomination, user, file, info, caption=""):
    """Store an upload as a pending entry.

    Raises ``images.ImageRejected`` for a file that fails to decode and
    ``LimitReached`` when the nomination quota is used up. Files written before
    a failure are removed.
    """
    # CPU-heavy work happens before taking the row lock.
    original, ext, width, height = images.make_original(file, info)
    preview = images.make_preview(file)

    entry = PhotoEntry(
        nomination=nomination,
        uploaded_by=user,
        caption=caption,
        width=width,
        height=height,
        size_bytes=len(original),
    )
    name = f"{entry_dir(nomination)}/{uuid4().hex}"
    written = []
    try:
        with transaction.atomic():
            locked = (
                PhotoNomination.objects.select_for_update(of=("self",))
                .select_related("contest")
                .get(pk=nomination.pk)
            )
            limit = locked.contest.max_photos_per_user
            if used_slots(locked, user) >= limit:
                raise LimitReached(
                    f"В номинации «{locked.title}» уже {limit} фото — это максимум."
                )
            entry.original.save(f"{name}.{ext}", ContentFile(original), save=False)
            written.append(entry.original)
            entry.preview.save(f"{name}.jpg", ContentFile(preview), save=False)
            written.append(entry.preview)
            entry.save()
    except BaseException:
        for field_file in written:
            field_file.storage.delete(field_file.name)
        raise
    return entry
