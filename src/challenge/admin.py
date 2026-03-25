from django.contrib import admin

from challenge.models import TelegramChat, TelegramMessage


@admin.register(TelegramChat)
class TelegramChatAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "telegram_id", "chat_type", "source_file", "updated")
    search_fields = ("name", "telegram_id", "source_file")
    list_filter = ("chat_type", "created", "updated")


@admin.register(TelegramMessage)
class TelegramMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "chat",
        "telegram_id",
        "message_type",
        "sender_name",
        "sent_at",
    )
    list_filter = ("message_type", "media_type", "sent_at")
    search_fields = (
        "text",
        "sender_name",
        "sender_id",
        "actor_name",
        "forwarded_from",
        "telegram_id",
    )
    autocomplete_fields = ("chat",)
