# Generated by Django 2.2.3 on 2019-07-08 12:35

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("website", "0008_teamadminlog_get_map"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="year",
            field=models.IntegerField(default=2018),
        ),
        migrations.CreateModel(
            name="Athlet",
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
                ("name", models.CharField(max_length=50)),
                ("birth", models.IntegerField(default=0)),
                ("number_in_team", models.IntegerField(default=0)),
                ("paid", models.FloatField(default=0)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="website.Team",
                    ),
                ),
            ],
        ),
    ]