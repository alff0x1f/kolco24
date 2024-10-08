# Generated by Django 3.2.19 on 2023-10-12 07:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0032_alter_takenkp_timestamp"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="team",
            name="finish_time",
        ),
        migrations.RemoveField(
            model_name="team",
            name="start_time",
        ),
        migrations.AlterField(
            model_name="takenkp",
            name="nfc",
            field=models.CharField(default="", max_length=300),
        ),
        migrations.AddField(
            model_name="team",
            name="finish_time",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="team",
            name="start_time",
            field=models.BigIntegerField(default=0),
        ),
    ]
