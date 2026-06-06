from django.contrib import admin

from apps.mobile.models import AppInstall


@admin.register(AppInstall)
class AppInstallAdmin(admin.ModelAdmin):
    list_display = (
        "install_id",
        "platform",
        "app_version",
        "request_count",
        "last_seen",
    )
    readonly_fields = (
        "install_id",
        "platform",
        "app_version",
        "first_seen",
        "last_seen",
        "last_ip",
        "request_count",
    )
    search_fields = ("install_id", "platform", "app_version")

    def has_add_permission(self, request):
        return False
