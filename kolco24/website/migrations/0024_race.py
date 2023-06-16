# Generated by Django 4.2.2 on 2023-06-16 17:52

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0023_auto_20230526_0951"),
    ]

    operations = [
        migrations.CreateModel(
            name="Race",
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
                ("name", models.CharField(max_length=50, verbose_name="Название")),
                ("code", models.CharField(max_length=15, verbose_name="Код")),
                (
                    "date",
                    models.DateField(
                        default=django.utils.timezone.now, verbose_name="Дата"
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активна"),
                ),
            ],
        ),
    ]
