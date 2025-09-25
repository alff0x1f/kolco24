from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("website", "0050_payment_map"),
    ]

    operations = [
        migrations.CreateModel(
            name="BusRegistration",
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
                ("full_name", models.CharField(max_length=255, verbose_name="Имя")),
                ("phone", models.CharField(max_length=64, verbose_name="Телефон")),
                (
                    "people_count",
                    models.PositiveIntegerField(verbose_name="Количество человек"),
                ),
                ("passengers", models.TextField(verbose_name="Кто поедет")),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "verbose_name": "Заявка на автобус",
                "verbose_name_plural": "Заявки на автобус",
            },
        ),
    ]
