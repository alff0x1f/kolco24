# Generated manually for TeamStartLog model

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0054_breakfastregistration"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamStartLog",
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
                    "start_number",
                    models.CharField(
                        blank=True, max_length=50, verbose_name="Стартовый номер"
                    ),
                ),
                (
                    "team_name",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="Название команды"
                    ),
                ),
                (
                    "participant_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Количество участников"
                    ),
                ),
                (
                    "scanned_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Сканировано браслетов"
                    ),
                ),
                (
                    "member_tags",
                    models.JSONField(
                        blank=True, default=list, verbose_name="Теги участников"
                    ),
                ),
                (
                    "start_timestamp",
                    models.BigIntegerField(verbose_name="Время старта (мс)"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "race",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_start_logs",
                        to="website.race",
                        verbose_name="Гонка",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="start_logs",
                        to="website.team",
                        verbose_name="Команда",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "verbose_name": "Старт команды",
                "verbose_name_plural": "Старты команд",
            },
        ),
        migrations.AddField(
            model_name="race",
            name="time_limit_min",
            field=models.IntegerField(default=0, verbose_name="Лимит по времени (мин)"),
        ),
    ]
