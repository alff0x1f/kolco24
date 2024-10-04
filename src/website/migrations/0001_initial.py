# Generated by Django 2.0.7 on 2018-07-20 23:41

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Profile",
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
                ("phone", models.TextField(blank=True, max_length=500)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Team",
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
                ("paymentid", models.CharField(max_length=50)),
                ("paid_sum", models.FloatField(default=0)),
                ("paid_people", models.FloatField(default=0)),
                ("dist", models.CharField(max_length=10)),
                ("ucount", models.IntegerField(default=1)),
                ("teamname", models.CharField(max_length=50)),
                ("city", models.CharField(max_length=50)),
                ("organization", models.CharField(max_length=50)),
                ("athlet1", models.CharField(max_length=50)),
                ("birth1", models.IntegerField(default=0)),
                ("athlet2", models.CharField(max_length=50)),
                ("birth2", models.IntegerField(default=0)),
                ("athlet3", models.CharField(max_length=50)),
                ("birth3", models.IntegerField(default=0)),
                ("athlet4", models.CharField(max_length=50)),
                ("birth4", models.IntegerField(default=0)),
                ("athlet5", models.CharField(max_length=50)),
                ("birth5", models.IntegerField(default=0)),
                ("athlet6", models.CharField(max_length=50)),
                ("birth6", models.IntegerField(default=0)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="YandexPayment",
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
                ("notification_type", models.CharField(max_length=20)),
                ("operation_id", models.CharField(max_length=50)),
                ("amount", models.CharField(max_length=10)),
                ("withdraw_amount", models.CharField(max_length=10)),
                ("currency", models.CharField(max_length=5)),
                ("datetime", models.CharField(max_length=20)),
                ("sender", models.CharField(max_length=50)),
                ("codepro", models.BooleanField(default=False)),
                ("label", models.CharField(max_length=50)),
                ("sha1_hash", models.CharField(max_length=30)),
                ("unaccepted", models.BooleanField(default=True)),
            ],
        ),
    ]