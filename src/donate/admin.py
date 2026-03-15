from django.contrib import admin

from donate.models import DonateRequest


@admin.register(DonateRequest)
class DonateRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "sender_name", "comment", "payment", "created")
    list_filter = ("comment", "created")
    search_fields = ("sender_name", "payment__order_id")
