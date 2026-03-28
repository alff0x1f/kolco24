import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from challenge.models import (
    Challenge,
    ChallengeActivity,
    ChallengeParticipant,
    TelegramChat,
    TelegramMessage,
)


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


class ChallengeActivityModelTestCase(TestCase):
    def setUp(self):
        self.challenge = Challenge.objects.create(
            name="Зимний челлендж 2025/2026",
            start_date=date(2025, 12, 1),
            end_date=date(2026, 3, 31),
        )
        self.participant = ChallengeParticipant.objects.create(
            challenge=self.challenge,
            telegram_user_id="user42",
            display_name="Ирек",
            group="ГШ-1",
        )
        self.chat = TelegramChat.objects.create(
            telegram_id=1,
            name="Чат челленджа",
            chat_type="private_supergroup",
        )
        self.message = TelegramMessage.objects.create(
            chat=self.chat,
            telegram_id=100,
            message_type="message",
            sent_at=timezone.now(),
            sender_name="Ирек",
            sender_id="user42",
            text="Бег 6 км\nЛыжи 12 км",
        )

    def test_run_points_respect_distance_and_pace_thresholds(self):
        short_run = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 12, 1),
            distance_km=Decimal("5.00"),
            pace_minutes_per_km=Decimal("9.59"),
        )
        long_run = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 12, 2),
            distance_km=Decimal("10.01"),
            pace_minutes_per_km=Decimal("9.59"),
        )
        slow_run = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 12, 3),
            distance_km=Decimal("8.00"),
            pace_minutes_per_km=Decimal("10.00"),
        )

        self.assertEqual(short_run.base_points, 0)
        self.assertEqual(long_run.base_points, 3)
        self.assertEqual(slow_run.base_points, 0)

    def test_other_activity_thresholds_follow_rules(self):
        ski = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_SKI,
            happened_on=date(2025, 12, 1),
            distance_km=Decimal("12.00"),
        )
        bike = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_BIKE,
            happened_on=date(2025, 12, 2),
            distance_km=Decimal("20.00"),
        )
        swim = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_SWIM,
            happened_on=date(2025, 12, 3),
            distance_km=Decimal("2.00"),
        )
        hike = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_HIKE_DAY,
            happened_on=date(2025, 12, 4),
        )

        self.assertEqual(ski.base_points, 3)
        self.assertEqual(bike.base_points, 2)
        self.assertEqual(swim.base_points, 3)
        self.assertEqual(hike.base_points, 2)

    def test_streak_bonus_uses_previous_scoring_activity_within_four_days(self):
        first_activity = ChallengeActivity.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 12, 1),
            distance_km=Decimal("6.00"),
            pace_minutes_per_km=Decimal("9.30"),
        )
        second_activity = ChallengeActivity.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_SKI,
            happened_on=date(2025, 12, 5),
            distance_km=Decimal("6.00"),
        )
        late_activity = ChallengeActivity.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_BIKE,
            happened_on=date(2025, 12, 11),
            distance_km=Decimal("20.00"),
        )

        self.assertEqual(first_activity.total_points, 2)
        self.assertEqual(second_activity.streak_bonus_points, 1)
        self.assertEqual(second_activity.total_points, 3)
        self.assertEqual(late_activity.streak_bonus_points, 0)
        self.assertEqual(late_activity.total_points, 2)

    def test_multiple_activities_can_reference_same_message(self):
        run_activity = ChallengeActivity.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            source_message=self.message,
            source_order=1,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 12, 1),
            distance_km=Decimal("6.00"),
            pace_minutes_per_km=Decimal("9.30"),
        )
        ski_activity = ChallengeActivity.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            source_message=self.message,
            source_order=2,
            activity_type=ChallengeActivity.TYPE_SKI,
            happened_on=date(2025, 12, 5),
            distance_km=Decimal("12.00"),
        )

        self.assertEqual(run_activity.source_message_id, self.message.id)
        self.assertEqual(ski_activity.source_message_id, self.message.id)
        self.assertEqual(self.message.challenge_activities.count(), 2)

    def test_one_activity_per_participant_per_day_is_enforced(self):
        ChallengeActivity.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 12, 1),
            distance_km=Decimal("6.00"),
            pace_minutes_per_km=Decimal("9.30"),
        )

        with self.assertRaises(IntegrityError):
            ChallengeActivity.objects.create(
                challenge=self.challenge,
                participant=self.participant,
                activity_type=ChallengeActivity.TYPE_SKI,
                happened_on=date(2025, 12, 1),
                distance_km=Decimal("12.00"),
            )

    def test_activity_must_belong_to_challenge_period(self):
        activity = ChallengeActivity(
            challenge=self.challenge,
            participant=self.participant,
            activity_type=ChallengeActivity.TYPE_RUN,
            happened_on=date(2025, 11, 30),
            distance_km=Decimal("6.00"),
            pace_minutes_per_km=Decimal("9.30"),
        )

        with self.assertRaises(ValidationError):
            activity.full_clean()
