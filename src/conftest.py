import pytest


@pytest.fixture(autouse=True)
def use_plain_static_storage(settings):
    storages = dict(getattr(settings, "STORAGES", {}))
    storages["staticfiles"] = {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    }
    settings.STORAGES = storages
