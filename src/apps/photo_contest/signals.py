from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.photo_contest.models import PhotoEntry


@receiver(post_delete, sender=PhotoEntry)
def delete_entry_files(sender, instance, **kwargs):
    # On commit: a rolled-back delete brings the row back, so its files must
    # still be there.
    files = [
        (field_file.storage, field_file.name)
        for field_file in (instance.original, instance.preview)
        if field_file.name
    ]

    def _delete():
        for storage, name in files:
            storage.delete(name)

    transaction.on_commit(_delete)
