# Generated by Django 4.2.14 on 2024-10-09 21:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0044_is_legend_visible_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="checkpoint",
            name="type",
            field=models.CharField(
                choices=[
                    ("start", "Старт"),
                    ("finish", "Финиш"),
                    ("test", "Тест"),
                    ("kp", "КП"),
                    ("draft", "Черновик"),
                ],
                default="kp",
                max_length=50,
                verbose_name="Тип точки",
            ),
        ),
    ]