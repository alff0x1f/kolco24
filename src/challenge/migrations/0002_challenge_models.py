from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("challenge", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Challenge",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="Название")),
                ("start_date", models.DateField(verbose_name="Дата начала")),
                ("end_date", models.DateField(verbose_name="Дата окончания")),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Челлендж",
                "verbose_name_plural": "Челленджи",
                "ordering": ("-start_date", "name"),
            },
        ),
        migrations.CreateModel(
            name="ChallengeParticipant",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "telegram_user_id",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="ID участника в Telegram",
                    ),
                ),
                ("display_name", models.CharField(max_length=255, verbose_name="Имя участника")),
                (
                    "group",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="Группа",
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "challenge",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participants",
                        to="challenge.challenge",
                        verbose_name="Челлендж",
                    ),
                ),
            ],
            options={
                "verbose_name": "Участник челленджа",
                "verbose_name_plural": "Участники челленджа",
                "ordering": ("display_name", "id"),
            },
        ),
        migrations.CreateModel(
            name="ChallengeActivity",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source_order",
                    models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Нужен, когда в одном сообщении указано несколько тренировок.",
                        verbose_name="Порядок в сообщении",
                    ),
                ),
                (
                    "activity_type",
                    models.CharField(
                        choices=[
                            ("run", "Бег"),
                            ("ski", "Лыжи"),
                            ("bike", "Велосипед"),
                            ("swim", "Плавание"),
                            ("hike_day", "Поход, полный день"),
                            ("gsh_day", "Тренировочный выезд ГШ, полный день"),
                        ],
                        max_length=32,
                        verbose_name="Тип активности",
                    ),
                ),
                ("happened_on", models.DateField(verbose_name="Дата активности")),
                (
                    "distance_km",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=6,
                        null=True,
                        verbose_name="Дистанция, км",
                    ),
                ),
                (
                    "pace_minutes_per_km",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Используется только для бега.",
                        max_digits=5,
                        null=True,
                        verbose_name="Темп, мин/км",
                    ),
                ),
                (
                    "comment",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="Комментарий",
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "challenge",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="activities",
                        to="challenge.challenge",
                        verbose_name="Челлендж",
                    ),
                ),
                (
                    "participant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="activities",
                        to="challenge.challengeparticipant",
                        verbose_name="Участник",
                    ),
                ),
                (
                    "source_message",
                    models.ForeignKey(
                        blank=True,
                        help_text="Исходное сообщение, из которого разобрана активность.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="challenge_activities",
                        to="challenge.telegrammessage",
                        verbose_name="Сообщение Telegram",
                    ),
                ),
            ],
            options={
                "verbose_name": "Активность челленджа",
                "verbose_name_plural": "Активности челленджа",
                "ordering": ("happened_on", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="challengeparticipant",
            constraint=models.UniqueConstraint(
                condition=Q(("telegram_user_id", ""), _negated=True),
                fields=("challenge", "telegram_user_id"),
                name="challenge_participant_telegram_id_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="challengeactivity",
            constraint=models.UniqueConstraint(
                fields=("challenge", "participant", "happened_on"),
                name="challenge_activity_unique_participant_day",
            ),
        ),
        migrations.AddConstraint(
            model_name="challengeactivity",
            constraint=models.UniqueConstraint(
                condition=Q(("source_message__isnull", False)),
                fields=("source_message", "source_order"),
                name="challenge_activity_unique_message_order",
            ),
        ),
        migrations.AddIndex(
            model_name="challengeactivity",
            index=models.Index(
                fields=["challenge", "participant", "happened_on"],
                name="challenge_activity_score_idx",
            ),
        ),
    ]
