# Generated by Django 4.2.14 on 2024-10-10 10:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0045_alter_checkpoint_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="race",
            name="is_photo_upload_enabled",
            field=models.BooleanField(
                default=False, verbose_name="Загрузка фото включена"
            ),
        ),
    ]
