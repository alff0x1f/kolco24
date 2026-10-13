from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction

from apps.photo_contest import images
from apps.photo_contest.models import PhotoEntry, PhotoNomination


class LimitReached(Exception):
    """The user already has the maximum number of photos in the nomination."""


class SubmissionClosed(Exception):
    """The contest stopped accepting photos while the upload was processed."""


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

    Raises ``images.ImageRejected`` for a file that fails to decode,
    ``SubmissionClosed`` when the contest closed or was unpublished meanwhile,
    and ``LimitReached`` when the nomination quota is used up. Files written
    before a failure are removed, partially written ones included.
    """
    # CPU-heavy work happens before taking the row lock.
    original, ext, width, height = images.make_original(file, info)
    preview = images.make_preview(file)
    # From the preview, not the original: no second full-size decode.
    thumb = images.make_preview(
        ContentFile(preview),
        max_side=images.THUMB_MAX_SIDE,
        quality=images.THUMB_QUALITY,
    )

    entry = PhotoEntry(
        nomination=nomination,
        uploaded_by=user,
        caption=caption,
        width=width,
        height=height,
        size_bytes=len(original),
    )
    name = f"{entry_dir(nomination)}/{uuid4().hex}"
    # Storage may leave a partial file when a write fails midway, so clean up
    # the target names as well as the names actually saved.
    targets = [
        (entry.original.storage, f"{name}.{ext}"),
        (entry.preview.storage, f"{name}.jpg"),
        (entry.thumb.storage, f"{name}_thumb.jpg"),
    ]
    written = []
    try:
        with transaction.atomic():
            locked = (
                PhotoNomination.objects.select_for_update(of=("self",))
                .select_related("contest")
                .get(pk=nomination.pk)
            )
            # Processing above can take seconds; the contest may have closed
            # meanwhile. The row is re-read here, inside the transaction.
            if not locked.contest.is_submission_open:
                raise SubmissionClosed
            limit = locked.contest.max_photos_per_user
            if used_slots(locked, user) >= limit:
                raise LimitReached(
                    f"В номинации «{locked.title}» уже {limit} фото — это максимум."
                )
            entry.original.save(targets[0][1], ContentFile(original), save=False)
            written.append((entry.original.storage, entry.original.name))
            entry.preview.save(targets[1][1], ContentFile(preview), save=False)
            written.append((entry.preview.storage, entry.preview.name))
            entry.thumb.save(targets[2][1], ContentFile(thumb), save=False)
            written.append((entry.thumb.storage, entry.thumb.name))
            entry.save()
    except BaseException:
        for storage, file_name in {*targets, *written}:
            storage.delete(file_name)
        raise
    return entry
