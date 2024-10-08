# Generated by Django 4.2.14 on 2024-09-28 23:24

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0041_remove_pointtag_race"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamMemberMove",
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
                ("moved_people", models.FloatField(default=0)),
                ("move_date", models.DateTimeField(auto_now_add=True)),
                (
                    "from_team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="moves_from",
                        to="website.team",
                    ),
                ),
                (
                    "to_team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="moves_to",
                        to="website.team",
                    ),
                ),
            ],
        ),
    ]
