from django.apps import AppConfig


class PhotoContestConfig(AppConfig):
    name = "apps.photo_contest"
    label = "photo_contest"
    verbose_name = "Фотоконкурс"

    def ready(self):
        from apps.photo_contest import signals  # noqa: F401
