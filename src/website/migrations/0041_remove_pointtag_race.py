# Generated by Django 4.2.14 on 2024-09-28 13:51

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0040_add_member_tag"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pointtag",
            name="race",
        ),
        migrations.RenameModel(
            old_name="PointTag",
            new_name="CheckpointTag",
        ),
    ]