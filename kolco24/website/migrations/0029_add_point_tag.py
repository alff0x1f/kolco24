# Generated by Django 3.2.19 on 2023-09-29 11:55

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0028_add_payment_balance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="team",
            name="teamname",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.CreateModel(
            name="PointTag",
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
                ("tag_id", models.CharField(max_length=255, unique=True)),
                (
                    "point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="website.controlpoint",
                        verbose_name="КП",
                    ),
                ),
            ],
            options={
                "verbose_name": "Тег",
                "verbose_name_plural": "Теги",
                "ordering": ["id"],
            },
        ),
    ]
