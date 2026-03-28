import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from challenge.models import TelegramChat, TelegramMessage


class ImportTelegramChatCommandTestCase(TestCase):
    def _write_export(self, payload):
        export_dir = Path(settings.BASE_DIR) / "tmp_test_exports"
        export_dir.mkdir(exist_ok=True)
        export_file = export_dir / "result.json"
        export_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return export_file

    def test_import_creates_chat_and_messages(self):
        export_file = self._write_export(
            {
                "id": 123456,
                "name": "Тестовый чат",
                "type": "private_supergroup",
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "date": "2026-03-20T10:15:30",
                        "edited": "2026-03-20T10:16:30",
                        "from": "Alice",
                        "from_id": "user1",
                        "text": "Привет",
                        "text_entities": [{"type": "plain", "text": "Привет"}],
                        "photo": "photos/p1.jpg",
                        "photo_file_size": 100,
                        "width": 800,
                        "height": 600,
                        "reactions": [{"emoji": "👍", "count": 1}],
                    },
                    {
                        "id": 2,
                        "type": "service",
                        "date": "2026-03-20T10:17:30",
                        "actor": "Bob",
                        "actor_id": "user2",
                        "action": "join_group_by_link",
                        "inviter": "Group",
                        "text": "",
                        "text_entities": [],
                    },
                    {
                        "id": 3,
                        "type": "message",
                        "date": "2026-03-20T10:18:30",
                        "from": "Carol",
                        "from_id": "user3",
                        "text": [
                            "Ссылка: ",
                            {"type": "link", "text": "https://example.com"},
                        ],
                        "text_entities": [
                            {"type": "plain", "text": "Ссылка: "},
                            {"type": "link", "text": "https://example.com"},
                        ],
                        "reply_to_message_id": 1,
                        "file": "files/doc.pdf",
                        "file_name": "doc.pdf",
                        "file_size": 200,
                        "mime_type": "application/pdf",
                        "media_type": "document",
                    },
                ],
            }
        )

        call_command("import_telegram_chat", str(export_file))

        chat = TelegramChat.objects.get(telegram_id=123456)
        self.assertEqual(chat.name, "Тестовый чат")
        self.assertEqual(chat.chat_type, "private_supergroup")
        self.assertEqual(chat.source_file, str(export_file))

        message = TelegramMessage.objects.get(chat=chat, telegram_id=1)
        self.assertEqual(message.sender_name, "Alice")
        self.assertEqual(message.text, "Привет")
        self.assertEqual(message.photo, "photos/p1.jpg")
        self.assertEqual(message.reactions, [{"emoji": "👍", "count": 1}])
        self.assertTrue(timezone.is_aware(message.sent_at))
        self.assertTrue(timezone.is_aware(message.edited_at))

        service_message = TelegramMessage.objects.get(chat=chat, telegram_id=2)
        self.assertEqual(service_message.actor_name, "Bob")
        self.assertEqual(service_message.action, "join_group_by_link")

        rich_text_message = TelegramMessage.objects.get(chat=chat, telegram_id=3)
        self.assertEqual(rich_text_message.text, "Ссылка: https://example.com")
        self.assertEqual(rich_text_message.reply_to_message_id, 1)
        self.assertEqual(rich_text_message.file_name, "doc.pdf")
        self.assertEqual(rich_text_message.raw_text[1]["text"], "https://example.com")

    def test_import_is_idempotent_and_updates_existing_messages(self):
        export_file = self._write_export(
            {
                "id": 123456,
                "name": "Тестовый чат",
                "type": "private_supergroup",
                "messages": [
                    {
                        "id": 10,
                        "type": "message",
                        "date": "2026-03-20T10:15:30",
                        "from": "Alice",
                        "from_id": "user1",
                        "text": "Первая версия",
                        "text_entities": [{"type": "plain", "text": "Первая версия"}],
                    }
                ],
            }
        )

        call_command("import_telegram_chat", str(export_file))

        export_file.write_text(
            json.dumps(
                {
                    "id": 123456,
                    "name": "Тестовый чат 2",
                    "type": "private_supergroup",
                    "messages": [
                        {
                            "id": 10,
                            "type": "message",
                            "date": "2026-03-20T10:15:30",
                            "from": "Alice",
                            "from_id": "user1",
                            "text": "Вторая версия",
                            "text_entities": [
                                {"type": "plain", "text": "Вторая версия"}
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        call_command("import_telegram_chat", str(export_file))

        self.assertEqual(TelegramChat.objects.count(), 1)
        self.assertEqual(TelegramMessage.objects.count(), 1)
        self.assertEqual(
            TelegramChat.objects.get(telegram_id=123456).name, "Тестовый чат 2"
        )
        self.assertEqual(
            TelegramMessage.objects.get(telegram_id=10).text, "Вторая версия"
        )
