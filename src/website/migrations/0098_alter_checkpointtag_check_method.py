from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0097_tag_code"),
    ]

    operations = [
        migrations.AlterField(
            model_name="checkpointtag",
            name="check_method",
            field=models.CharField(
                choices=[
                    ("offline", "Офлайн"),
                    ("cloud", "Облако"),
                    ("local", "Локальный сервер"),
                ],
                default="offline",
                max_length=20,
                verbose_name="Метод проверки",
            ),
        ),
    ]
