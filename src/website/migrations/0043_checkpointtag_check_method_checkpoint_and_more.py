# Generated by Django 4.2.14 on 2024-10-04 12:08

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0042_teammembermove"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkpointtag",
            name="check_method",
            field=models.CharField(
                choices=[
                    ("offline", "Offline"),
                    ("online", "Online"),
                    ("local_server", "Local Server"),
                ],
                default="offline",
                max_length=20,
                verbose_name="Метод проверки",
            ),
        ),
        migrations.AddField(
            model_name="controlpoint",
            name="type",
            field=models.CharField(
                choices=[
                    ("start", "Старт"),
                    ("finish", "Финиш"),
                    ("test", "Тест"),
                    ("kp", "КП"),
                ],
                default="kp",
                max_length=50,
                verbose_name="Тип точки",
            ),
        ),
        migrations.RenameModel(
            old_name="ControlPoint",
            new_name="Checkpoint",
        ),
        migrations.AlterField(
            model_name="checkpointtag",
            name="point",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="website.checkpoint",
                verbose_name="КП",
            ),
        ),
    ]
