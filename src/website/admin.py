from django.contrib import admin
from django.utils.html import format_html
from markdown import markdown

from .models import (
    Checkpoint,
    CheckpointTag,
    MenuItem,
    NewsPost,
    Page,
    Payment,
    PaymentsYa,
    Race,
    RaceAdmin,
    SbpPaymentRecipient,
    Tag,
    TakenKP,
    Team,
    TeamFinishLog,
    TeamMemberRaceLog,
    TeamStartLog,
)
from .models.race import Category, RaceLink, RacePriceTier


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "url", "order")
    ordering = ("order",)


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "slug")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("content_html",)

    fieldsets = ((None, {"fields": ("title", "slug", "content", "content_html")}),)


@admin.register(TeamStartLog)
class TeamStartLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "race",
        "team",
        "start_number",
        "participant_count",
        "scanned_count",
        "start_timestamp",
        "created_at",
    )
    list_filter = ("race", "created_at")
    search_fields = ("team_name", "start_number")


@admin.register(TeamFinishLog)
class TeamFinishLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "race",
        "team",
        "member_tag_id",
        "tag_uid",
        "recorded_at",
        "created_at",
    )
    list_filter = ("race", "created_at")
    search_fields = ("tag_uid",)


@admin.register(TeamMemberRaceLog)
class TeamMemberRaceLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "race",
        "member_tag",
        "start_time",
        "finish_time",
    )
    list_filter = ("race",)
    search_fields = ("member_tag__nfc_uid", "member_tag__number")


class TeamAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "paymentid",
        "owner",
        "teamname",
        "paid_people",
        "dist",
        "year",
    )
    list_filter = ("year", "category", "category2")


class PointTagInline(admin.TabularInline):
    model = CheckpointTag
    extra = 1
    readonly_fields = ("bid",)
    exclude = ("code", "bundle_blob", "unlocks")


@admin.register(Checkpoint)
class CheckpointAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "year",
        "iterator",
        "number",
        "color",
        "cost",
        "description",
        "is_legend_locked",
        "race",
    )
    list_filter = ("race", "year", "color", "cost", "is_legend_locked")
    inlines = [PointTagInline]
    actions = ["lock_legend", "unlock_legend"]

    @admin.action(description="Запереть легенду")
    def lock_legend(self, request, queryset):
        # MUST iterate + save() each object so the post_save signal seals the
        # CheckpointSecret (and rebuilds dependent bundles). A queryset.update()
        # would skip the signal, leaving locked КП without a secret — the
        # serializer would then fall back to the open branch and leak cleartext.
        count = 0
        for cp in queryset:
            cp.is_legend_locked = True
            cp.save()
            count += 1
        self.message_user(request, f"Заперто КП: {count}")

    @admin.action(description="Открыть легенду")
    def unlock_legend(self, request, queryset):
        count = 0
        for cp in queryset:
            cp.is_legend_locked = False
            cp.save()
            count += 1
        self.message_user(request, f"Открыто КП: {count}")


@admin.register(CheckpointTag)
class CheckpointTagAdmin(admin.ModelAdmin):
    list_display = ("id", "point", "point_number", "nfc_uid", "bid")
    list_filter = ("point__race",)
    filter_horizontal = ("unlocks",)
    readonly_fields = ("bid", "code_hex")
    actions = ["regenerate_code", "rebuild_bundle"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("point")

    def point_number(self, obj) -> int:
        return obj.point.number

    @admin.display(description="Код (hex)")
    def code_hex(self, obj) -> str:
        return bytes(obj.code).hex() if obj.code else ""

    @admin.action(description="Перегенерировать код")
    def regenerate_code(self, request, queryset):
        from apps.mobile.legend_crypto import build_bundle

        count = 0
        for tag in queryset.select_related("point"):
            tag.code = None  # forces ensure_code to mint a fresh code
            build_bundle(tag)
            count += 1
        self.message_user(request, f"Перегенерирован код у тегов: {count}")

    @admin.action(description="Пересобрать бандл")
    def rebuild_bundle(self, request, queryset):
        from apps.mobile.legend_crypto import build_bundle

        count = 0
        for tag in queryset.select_related("point"):
            build_bundle(tag)
            count += 1
        self.message_user(request, f"Пересобрано бандлов: {count}")


class TakenKPAdmin(admin.ModelAdmin):
    list_display = ("team", "point_number", "status")
    list_filter = ("team", "point_number", "status")


class PaymentsYaAdmin(admin.ModelAdmin):
    list_display = ("label", "amount", "datetime", "unaccepted")
    list_filter = ("datetime", "amount")


class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "team",
        "payment_amount",
        "cost_per_person",
        "paid_for",
        "status",
    )


class RaceAdminInline(admin.TabularInline):
    model = RaceAdmin
    extra = 1


class RacePriceTierInline(admin.TabularInline):
    model = RacePriceTier
    extra = 1


class RaceModelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "date", "is_published", "reg_status")
    list_filter = ("is_published",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [RaceAdminInline, RacePriceTierInline]


class RaceLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "url")
    list_filter = ("race",)


class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "short_name",
        "order",
        "min_people",
        "max_people",
        "is_active",
    )
    list_filter = ("race__name", "is_active")
    search_fields = ("code", "name")


class NewsPostAdmin(admin.ModelAdmin):
    list_display = ("title", "publication_date", "race", "created_at", "updated_at")
    search_fields = ("title", "content")
    list_filter = ("race", "publication_date")
    readonly_fields = ("created_at", "updated_at", "content_html")

    # Fieldsets for better organization in the admin form
    fieldsets = (
        (None, {"fields": ("title", "content", "content_html", "image", "race")}),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    # Customize the form to exclude content_html and show markdown preview
    def save_model(self, request, obj, form, change):
        # Render the markdown content to HTML before saving
        obj.content_html = markdown(obj.content)
        super().save_model(request, obj, form, change)

    # Optionally, show the HTML content in the list view as a preview
    def content_preview(self, obj):
        return format_html(obj.content_html)

    content_preview.short_description = "HTML Preview"


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("number", "nfc_uid", "last_seen_at")
    search_fields = ("number", "nfc_uid")


admin.site.register(SbpPaymentRecipient)
admin.site.register(Team, TeamAdmin)
admin.site.register(TakenKP, TakenKPAdmin)
admin.site.register(PaymentsYa, PaymentsYaAdmin)
admin.site.register(Payment, PaymentAdmin)
admin.site.register(Race, RaceModelAdmin)
admin.site.register(Category, CategoryAdmin)
admin.site.register(NewsPost, NewsPostAdmin)
admin.site.register(RaceLink, RaceLinkAdmin)
