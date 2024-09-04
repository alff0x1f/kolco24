from django.contrib import admin
from django.utils.html import format_html
from markdown import markdown

from .models import ControlPoint, NewsPost, Payment, PaymentsYa, Race, TakenKP, Team
from .models.race import Category


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


class ControlPointAdmin(admin.ModelAdmin):
    list_display = ("iterator", "number", "cost")
    list_filter = ("year", "cost")


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
    list_display = ("name", "code", "date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


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


admin.site.register(Team, TeamAdmin)
admin.site.register(ControlPoint, ControlPointAdmin)
admin.site.register(TakenKP, TakenKPAdmin)
admin.site.register(PaymentsYa, PaymentsYaAdmin)
admin.site.register(Payment, PaymentAdmin)
admin.site.register(Race, RaceAdmin)
admin.site.register(Category, CategoryAdmin)
admin.site.register(NewsPost, NewsPostAdmin)
