from django.contrib import admin

from .models import ControlPoint, Payment, PaymentsYa, TakenKP, Team


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
    list_filter = ("year", "category")


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


admin.site.register(Team, TeamAdmin)
admin.site.register(ControlPoint, ControlPointAdmin)
admin.site.register(TakenKP, TakenKPAdmin)
admin.site.register(PaymentsYa, PaymentsYaAdmin)
admin.site.register(Payment, PaymentAdmin)
