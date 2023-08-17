from django.contrib import admin

from .models import ControlPoint, Payment, PaymentsYa, Race, TakenKP, Team
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
    list_display = ("code", "name", "short_name", "order", "is_active")
    list_filter = ("race__name", "is_active")
    search_fields = ("code", "name")


admin.site.register(Team, TeamAdmin)
admin.site.register(ControlPoint, ControlPointAdmin)
admin.site.register(TakenKP, TakenKPAdmin)
admin.site.register(PaymentsYa, PaymentsYaAdmin)
admin.site.register(Payment, PaymentAdmin)
admin.site.register(Race, RaceAdmin)
admin.site.register(Category, CategoryAdmin)
