import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from challenge.models import TelegramChat, TelegramMessage


def _normalize_datetime(value):
    if not value:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"Не удалось разобрать дату: {value}")

    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())

    return parsed


def _normalize_text(value):
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue

            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
                continue

            parts.append(str(item))

        return "".join(parts)

    if value is None:
        return ""

    return str(value)


class Command(BaseCommand):
    help = (
        "Импортирует Telegram Desktop export в challenge.TelegramChat/TelegramMessage."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "json_path",
            nargs="?",
            default="export_telegram/ChatExport_2026-03-23/result.json",
            help="Путь к result.json из Telegram Desktop export.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        json_path = Path(options["json_path"]).expanduser()
        if not json_path.is_absolute():
            json_path = Path.cwd() / json_path

        if not json_path.exists():
            raise CommandError(f"Файл не найден: {json_path}")

        try:
            with json_path.open(encoding="utf-8") as source:
                payload = json.load(source)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Некорректный JSON в {json_path}: {exc}") from exc

        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise CommandError("В файле отсутствует массив messages")

        chat_id = payload.get("id")
        if chat_id is None:
            raise CommandError("В файле отсутствует id чата")

        chat_defaults = {
            "name": payload.get("name") or str(chat_id),
            "chat_type": payload.get("type") or "",
            "source_file": str(json_path),
            "raw": {key: value for key, value in payload.items() if key != "messages"},
        }
        chat, chat_created = TelegramChat.objects.update_or_create(
            telegram_id=chat_id,
            defaults=chat_defaults,
        )

        created_messages = 0
        updated_messages = 0

        for message in messages:
            message_id = message.get("id")
            if message_id is None:
                continue

            message_defaults = {
                "message_type": message.get("type") or "",
                "sent_at": _normalize_datetime(message.get("date")),
                "edited_at": _normalize_datetime(message.get("edited")),
                "sender_name": message.get("from") or "",
                "sender_id": message.get("from_id") or "",
                "actor_name": message.get("actor") or "",
                "actor_id": message.get("actor_id") or "",
                "action": message.get("action") or "",
                "inviter": message.get("inviter") or "",
                "reply_to_message_id": message.get("reply_to_message_id"),
                "forwarded_from": message.get("forwarded_from") or "",
                "forwarded_from_id": message.get("forwarded_from_id") or "",
                "via_bot": message.get("via_bot") or "",
                "text": _normalize_text(message.get("text")),
                "raw_text": message.get("text"),
                "text_entities": message.get("text_entities") or [],
                "reactions": message.get("reactions") or [],
                "media_type": message.get("media_type") or "",
                "photo": message.get("photo") or "",
                "file": message.get("file") or "",
                "file_name": message.get("file_name") or "",
                "mime_type": message.get("mime_type") or "",
                "thumbnail": message.get("thumbnail") or "",
                "title": message.get("title") or "",
                "performer": message.get("performer") or "",
                "sticker_emoji": message.get("sticker_emoji") or "",
                "duration_seconds": message.get("duration_seconds"),
                "width": message.get("width"),
                "height": message.get("height"),
                "photo_file_size": message.get("photo_file_size"),
                "file_size": message.get("file_size"),
                "thumbnail_file_size": message.get("thumbnail_file_size"),
                "live_location_period_seconds": message.get(
                    "live_location_period_seconds"
                ),
                "media_spoiler": bool(message.get("media_spoiler", False)),
                "members": message.get("members") or [],
                "poll": message.get("poll"),
                "inline_bot_buttons": message.get("inline_bot_buttons") or [],
                "location_information": message.get("location_information"),
                "raw": message,
            }

            _, created = TelegramMessage.objects.update_or_create(
                chat=chat,
                telegram_id=message_id,
                defaults=message_defaults,
            )
            if created:
                created_messages += 1
            else:
                updated_messages += 1

        action = "создан" if chat_created else "обновлен"
        self.stdout.write(
            self.style.SUCCESS(
                f"Чат {chat.telegram_id} {action}: "
                f"создано сообщений {created_messages}, обновлено {updated_messages}"
            )
        )
