import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Storage for files that must never be served by nginx (photo originals).

    The root is read from ``settings.MEDIA_PRIVATE_ROOT`` on every access:
    Django builds a field's storage once, at model class creation, so a
    location captured then would ignore later settings overrides (tests).
    ``url()`` raises so nothing can build a public ``/media/`` link to a
    private file — originals go through the ``photo_original`` view.
    """

    @property
    def base_location(self):
        return settings.MEDIA_PRIVATE_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise NotImplementedError("Private files have no public URL.")


def private_storage():
    return PrivateStorage()
