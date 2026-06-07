from django.db import models


class AppInstall(models.Model):
    """Per-install usage stats for the mobile app.

    One row per app-generated ``install_id`` (a UUID created on first launch and
    stored locally by the client). Updated best-effort on each verified request.
    """

    install_id = models.CharField(max_length=64, unique=True)
    platform = models.CharField(max_length=16, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    request_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.install_id} ({self.platform})"
