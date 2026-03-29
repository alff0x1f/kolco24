import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from challenge.models import (
    Challenge,
    ChallengeMessageBatchReview,
    ChallengeParticipant,
    ChallengeTrainingLabel,
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


class ChallengeTrainingLabelModelTestCase(TestCase):
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

    def create_label(self, training_date, training_type, decision="counted"):
        return ChallengeTrainingLabel.objects.create(
            challenge=self.challenge,
            participant=self.participant,
            training_date=training_date,
            decision=decision,
            training_type=training_type,
        )

    def test_type_points_follow_bucket_rules(self):
        label = self.create_label(
            date(2025, 12, 1),
            ChallengeTrainingLabel.TrainingType.SKI_12_PLUS,
        )

        self.assertEqual(label.base_points, 3)
        self.assertEqual(label.streak_bonus_points, 0)
        self.assertEqual(label.total_points, 3)

    def test_not_counted_training_has_zero_points(self):
        label = self.create_label(
            date(2025, 12, 1),
            ChallengeTrainingLabel.TrainingType.RUN_10_PLUS,
            decision=ChallengeTrainingLabel.Decision.NOT_COUNTED,
        )

        self.assertEqual(label.base_points, 0)
        self.assertEqual(label.streak_bonus_points, 0)
        self.assertEqual(label.total_points, 0)

    def test_streak_bonus_uses_previous_counted_label_within_four_days(self):
        first_label = self.create_label(
            date(2025, 12, 1),
            ChallengeTrainingLabel.TrainingType.RUN_5_10,
        )
        second_label = self.create_label(
            date(2025, 12, 5),
            ChallengeTrainingLabel.TrainingType.SWIM_1_2,
        )
        third_label = self.create_label(
            date(2025, 12, 10),
            ChallengeTrainingLabel.TrainingType.BIKE_20_40,
        )

        self.assertEqual(first_label.total_points, 2)
        self.assertEqual(second_label.streak_bonus_points, 1)
        self.assertEqual(second_label.total_points, 3)
        self.assertEqual(third_label.streak_bonus_points, 0)
        self.assertEqual(third_label.total_points, 2)

    def test_one_label_per_participant_per_day_is_enforced(self):
        self.create_label(date(2025, 12, 1), ChallengeTrainingLabel.TrainingType.RUN_5_10)

        with self.assertRaises(IntegrityError):
            self.create_label(
                date(2025, 12, 1),
                ChallengeTrainingLabel.TrainingType.SKI_6_12,
            )

    def test_label_must_belong_to_challenge_period(self):
        label = ChallengeTrainingLabel(
            challenge=self.challenge,
            participant=self.participant,
            training_date=date(2025, 11, 30),
            decision=ChallengeTrainingLabel.Decision.COUNTED,
            training_type=ChallengeTrainingLabel.TrainingType.RUN_5_10,
        )

        with self.assertRaises(ValidationError):
            label.full_clean()


class ChallengeMessageMarkupViewTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.challenge = Challenge.objects.create(
            name="Весенний челлендж",
            start_date=self.today - timedelta(days=30),
            end_date=self.today + timedelta(days=30),
        )
        self.participant = ChallengeParticipant.objects.create(
            challenge=self.challenge,
            telegram_user_id="user42",
            display_name="Ирек",
        )
        self.chat = TelegramChat.objects.create(
            telegram_id=1,
            name="Чат челленджа",
            chat_type="private_supergroup",
        )
        self.staff_user = get_user_model().objects.create_user(
            username="staff",
            password="secret",
            is_staff=True,
        )
        self.plain_user = get_user_model().objects.create_user(
            username="plain",
            password="secret",
            is_staff=False,
        )
        self.batch_day_1 = self.today - timedelta(days=2)
        self.batch_day_2 = self.today - timedelta(days=1)

        self.message_1 = self.create_message(
            telegram_id=10,
            message_day=self.batch_day_1,
            text="Вчера бег, сегодня плавание",
        )
        self.message_2 = self.create_message(
            telegram_id=11,
            message_day=self.batch_day_1,
            text="Еще одно уточнение по тем же дням",
        )
        self.message_3 = self.create_message(
            telegram_id=12,
            message_day=self.batch_day_2,
            text="Тут просто болтовня",
        )

    def create_message(self, telegram_id, message_day, text):
        sent_at = timezone.make_aware(datetime.combine(message_day, time(12, 0)))
        return TelegramMessage.objects.create(
            chat=self.chat,
            telegram_id=telegram_id,
            message_type="message",
            sent_at=sent_at,
            sender_name=self.participant.display_name,
            sender_id=self.participant.telegram_user_id,
            text=text,
        )

    def markup_url(self, participant=None, message_day=None):
        url = reverse("challenge_messages_markup")
        query = [f"challenge={self.challenge.id}"]
        if participant is not None:
            query.append(f"participant={participant.id}")
        if message_day is not None:
            query.append(f"message_day={message_day.isoformat()}")
        return f"{url}?{'&'.join(query)}"

    def test_non_staff_user_is_denied(self):
        self.client.login(username="plain", password="secret")

        response = self.client.get(self.markup_url())

        self.assertEqual(response.status_code, 302)

    def test_page_loads_next_unreviewed_batch(self):
        self.client.login(username="staff", password="secret")

        response = self.client.get(self.markup_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.batch_day_1.isoformat())
        self.assertContains(response, "Вчера бег, сегодня плавание")

    def test_can_add_several_labels_from_one_batch_and_finish_it(self):
        self.client.login(username="staff", password="secret")

        batch_url = self.markup_url(self.participant, self.batch_day_1)
        response = self.client.post(
            batch_url,
            {
                "action": "add_label",
                "challenge": self.challenge.id,
                "participant": self.participant.id,
                "message_day": self.batch_day_1.isoformat(),
                "training_date": (self.today - timedelta(days=3)).isoformat(),
                "decision": ChallengeTrainingLabel.Decision.COUNTED,
                "training_type": ChallengeTrainingLabel.TrainingType.RUN_10_PLUS,
                "comment": "Позавчерашняя тренировка",
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            batch_url,
            {
                "action": "add_label",
                "challenge": self.challenge.id,
                "participant": self.participant.id,
                "message_day": self.batch_day_1.isoformat(),
                "training_date": self.today.isoformat(),
                "decision": ChallengeTrainingLabel.Decision.NOT_COUNTED,
                "training_type": ChallengeTrainingLabel.TrainingType.SWIM_1_2,
                "comment": "Сегодня не в зачет",
            },
        )
        self.assertEqual(response.status_code, 302)

        labels = ChallengeTrainingLabel.objects.order_by("training_date")
        self.assertEqual(labels.count(), 2)
        self.assertEqual(labels[0].source_messages.count(), 2)
        self.assertEqual(labels[1].source_messages.count(), 2)
        self.assertEqual(labels[0].reviewed_by, self.staff_user)
        self.assertEqual(labels[1].total_points, 0)

        response = self.client.post(
            batch_url,
            {
                "action": "finish_batch",
                "challenge": self.challenge.id,
                "participant": self.participant.id,
                "message_day": self.batch_day_1.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)

        review = ChallengeMessageBatchReview.objects.get(
            challenge=self.challenge,
            participant=self.participant,
            message_day=self.batch_day_1,
        )
        self.assertEqual(review.resolution, ChallengeMessageBatchReview.Resolution.LABELED)
        self.assertEqual(review.reviewed_by, self.staff_user)

    def test_flood_marks_batch_as_processed_and_moves_queue(self):
        self.client.login(username="staff", password="secret")

        batch_url = self.markup_url(self.participant, self.batch_day_1)
        response = self.client.post(
            batch_url,
            {
                "action": "mark_flood",
                "challenge": self.challenge.id,
                "participant": self.participant.id,
                "message_day": self.batch_day_1.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)

        review = ChallengeMessageBatchReview.objects.get(
            challenge=self.challenge,
            participant=self.participant,
            message_day=self.batch_day_1,
        )
        self.assertEqual(review.resolution, ChallengeMessageBatchReview.Resolution.FLOOD)
        self.assertEqual(review.reviewed_by, self.staff_user)

        response = self.client.get(self.markup_url())
        self.assertContains(response, self.batch_day_2.isoformat())
        self.assertContains(response, "Тут просто болтовня")

    def test_finish_requires_at_least_one_label(self):
        self.client.login(username="staff", password="secret")

        batch_url = self.markup_url(self.participant, self.batch_day_1)
        response = self.client.post(
            batch_url,
            {
                "action": "finish_batch",
                "challenge": self.challenge.id,
                "participant": self.participant.id,
                "message_day": self.batch_day_1.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сначала добавьте хотя бы одну тренировку")
