from django.contrib import admin

from apps.mobile.models import AppAuthFailure, AppInstall


@admin.register(AppInstall)
class AppInstallAdmin(admin.ModelAdmin):
    list_display = (
        "install_id",
        "platform",
        "app_version",
        "key_id",
        "request_count",
        "last_seen",
    )
    list_filter = ("key_id", "platform")
    readonly_fields = (
        "install_id",
        "platform",
        "app_version",
        "key_id",
        "first_seen",
        "last_seen",
        "last_ip",
        "request_count",
    )
    search_fields = ("install_id", "platform", "app_version")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AppAuthFailure)
class AppAuthFailureAdmin(admin.ModelAdmin):
    list_display = (
        "ip",
        "key_id",
        "reason",
        "count",
        "last_seen",
        "last_path",
    )
    list_filter = ("reason", "key_id")
    ordering = ("-count",)
    search_fields = ("ip", "key_id")
    readonly_fields = (
        "ip",
        "key_id",
        "reason",
        "count",
        "first_seen",
        "last_seen",
        "last_path",
        "last_install_id",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
