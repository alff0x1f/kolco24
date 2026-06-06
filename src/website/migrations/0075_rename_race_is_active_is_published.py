from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0074_remove_race_code"),
    ]

    operations = [
        migrations.RenameField(
            model_name="race",
            old_name="is_active",
            new_name="is_published",
        ),
        migrations.AlterField(
            model_name="race",
            name="is_published",
            field=models.BooleanField(default=True, verbose_name="Опубликована"),
        ),
    ]
