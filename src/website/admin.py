from django.contrib import admin
from django.utils.html import format_html
from markdown import markdown

from .models import (
    BreakfastRegistration,
    Checkpoint,
    CheckpointTag,
    MenuItem,
    NewsPost,
    Page,
    Payment,
    PaymentsYa,
    Race,
    SbpPaymentRecipient,
    Tag,
    TakenKP,
    Team,
    Transfer,
)
from .models.race import Category, RaceLink


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


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "people_count",
        "participants_display",
        "created_at",
        "status",
    )
    list_filter = ("created_at", "status")
    ordering = ("-created_at",)

    @admin.display(description="Участники")
    def participants_display(self, obj):
        if not obj.passenger_contacts:
            return "—"
        parts = []
        for contact in obj.passenger_contacts:
            name = (contact or {}).get("name", "").strip()
            phone = (contact or {}).get("phone", "").strip()
            if not name and not phone:
                continue
            if phone:
                parts.append(f"{name} ({phone})" if name else phone)
            else:
                parts.append(name)
        return ", ".join(parts) if parts else "—"


@admin.register(BreakfastRegistration)
class BreakfastRegistrationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "race",
        "people_count",
        "attendees_display",
        "vegan_count",
        "created_at",
        "status",
    )
    list_filter = ("race", "created_at", "status")
    search_fields = ("attendees",)
    ordering = ("-created_at",)

    @admin.display(description="Участники")
    def attendees_display(self, obj):
        if not obj.attendees:
            return "—"
        parts = []
        for attendee in obj.attendees:
            attendee = attendee or {}
            name = attendee.get("name", "").strip()
            vegan = attendee.get("is_vegan")
            vegan_label = " (веган)" if vegan else ""
            if name:
                parts.append(f"{name}{vegan_label}")
        return ", ".join(parts) if parts else "—"

    @admin.display(description="Веганы")
    def vegan_count(self, obj):
        if not obj.attendees:
            return 0
        return sum(1 for attendee in obj.attendees if attendee and attendee.get("is_vegan"))


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


@admin.register(Checkpoint)
class CheckpointAdmin(admin.ModelAdmin):
    list_display = ("id", "year", "iterator", "number", "cost", "description", "race")
    list_filter = ("race", "year", "cost")
    inlines = [PointTagInline]


@admin.register(CheckpointTag)
class CheckpointTagAdmin(admin.ModelAdmin):
    list_display = ("id", "point", "point_number", "tag_id")
    list_filter = ("point__race",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("point")

    def point_number(self, obj) -> int:
        return obj.point.number


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


class RaceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "date", "is_active", "is_reg_open")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


class RaceLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "url")
    list_filter = ("race",)


class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "short_name", "order", "is_active")
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
    list_display = ("number", "tag_id", "last_seen_at")
    search_fields = ("number", "tag_id")


admin.site.register(SbpPaymentRecipient)
admin.site.register(Team, TeamAdmin)
admin.site.register(TakenKP, TakenKPAdmin)
admin.site.register(PaymentsYa, PaymentsYaAdmin)
admin.site.register(Payment, PaymentAdmin)
admin.site.register(Race, RaceAdmin)
admin.site.register(Category, CategoryAdmin)
admin.site.register(NewsPost, NewsPostAdmin)
admin.site.register(RaceLink, RaceLinkAdmin)
