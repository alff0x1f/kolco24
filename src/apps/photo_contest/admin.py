import os

from django.conf import settings
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.photo_contest.models import PhotoContest, PhotoEntry, PhotoNomination


class PhotoNominationInline(admin.TabularInline):
    model = PhotoNomination
    extra = 0
    fields = ["title", "description", "order"]


@admin.register(PhotoContest)
class PhotoContestAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "submission_until", "is_published"]
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ["folders"]
    inlines = [PhotoNominationInline]

    @admin.display(description="Папки с файлами")
    def folders(self, obj):
        if not obj.pk:
            return "—"
        folder = f"photo_contest/{obj.pk}/"
        return format_html(
            "Оригиналы: <code>{}</code><br>Превью: <code>{}</code>",
            os.path.join(settings.MEDIA_PRIVATE_ROOT, folder),
            os.path.join(settings.MEDIA_ROOT, folder),
        )


@admin.register(PhotoEntry)
class PhotoEntryAdmin(admin.ModelAdmin):
    list_display = [
        "thumbnail",
        "contest",
        "nomination",
        "author",
        "status",
        "created_at",
    ]
    list_display_links = ["thumbnail"]
    list_filter = ["nomination__contest", "nomination", "status"]
    list_select_related = ["nomination__contest", "uploaded_by"]
    search_fields = ["uploaded_by__email", "caption"]
    actions = ["approve", "reject"]
    fields = [
        "preview_large",
        "original_link",
        "nomination",
        "uploaded_by",
        "caption",
        "status",
        "width",
        "height",
        "size_bytes",
        "created_at",
    ]
    # Files are produced by the upload pipeline (resize, EXIF strip) and laid
    # out by nomination id, so neither the files nor the nomination are edited
    # here.
    readonly_fields = [
        "preview_large",
        "original_link",
        "nomination",
        "uploaded_by",
        "width",
        "height",
        "size_bytes",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Фото")
    def thumbnail(self, obj):
        return format_html(
            '<img src="{}" style="height:80px;border-radius:4px">', obj.preview.url
        )

    @admin.display(description="Превью")
    def preview_large(self, obj):
        return format_html(
            '<img src="{}" style="max-width:600px;max-height:600px">',
            obj.preview.url,
        )

    @admin.display(description="Оригинал")
    def original_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">Открыть оригинал</a>',
            reverse("photo_original", args=[obj.pk]),
        )

    @admin.display(description="Конкурс", ordering="nomination__contest")
    def contest(self, obj):
        return obj.nomination.contest

    @admin.display(description="Автор", ordering="uploaded_by__email")
    def author(self, obj):
        return obj.uploaded_by.email

    @admin.action(description="Принять")
    def approve(self, request, queryset):
        updated = queryset.update(status=PhotoEntry.APPROVED)
        self.message_user(request, f"Принято: {updated}")

    @admin.action(description="Отклонить")
    def reject(self, request, queryset):
        updated = queryset.update(status=PhotoEntry.REJECTED)
        self.message_user(request, f"Отклонено: {updated}")
