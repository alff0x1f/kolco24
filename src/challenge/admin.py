from django.contrib import admin

from challenge.models import (
    Challenge,
    ChallengeMessageBatchReview,
    ChallengeParticipant,
    ChallengeTrainingLabel,
    ChallengeTrainingLabelMessage,
    TelegramChat,
    TelegramMessage,
)


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


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "start_date", "end_date", "updated")
    search_fields = ("name",)


@admin.register(ChallengeParticipant)
class ChallengeParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "group",
        "challenge",
        "telegram_user_id",
        "updated",
    )
    list_filter = ("challenge", "group")
    search_fields = ("display_name", "group", "telegram_user_id")
    autocomplete_fields = ("challenge",)


class ChallengeTrainingLabelMessageInline(admin.TabularInline):
    model = ChallengeTrainingLabelMessage
    extra = 0
    autocomplete_fields = ("message",)


@admin.register(ChallengeTrainingLabel)
class ChallengeTrainingLabelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "participant",
        "challenge",
        "training_date",
        "decision",
        "training_type",
        "base_points",
        "streak_bonus_points",
        "total_points",
        "reviewed_by",
    )
    list_filter = ("challenge", "decision", "training_type", "training_date")
    search_fields = ("participant__display_name", "comment")
    autocomplete_fields = ("challenge", "participant", "reviewed_by")
    inlines = (ChallengeTrainingLabelMessageInline,)


@admin.register(ChallengeTrainingLabelMessage)
class ChallengeTrainingLabelMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "label", "message", "created")
    autocomplete_fields = ("label", "message")


@admin.register(ChallengeMessageBatchReview)
class ChallengeMessageBatchReviewAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "participant",
        "challenge",
        "message_day",
        "resolution",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("challenge", "resolution", "message_day")
    search_fields = ("participant__display_name", "comment")
    autocomplete_fields = ("challenge", "participant", "reviewed_by")
