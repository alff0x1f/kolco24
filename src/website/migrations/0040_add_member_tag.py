# Generated by Django 4.2.14 on 2024-09-26 19:10
import django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0039_add_race_to_controlpoint"),
    ]

    operations = [
        migrations.CreateModel(
            name="Tag",
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
                ("number", models.IntegerField(verbose_name="Номер")),
                (
                    "tag_id",
                    models.CharField(
                        max_length=255, unique=True, verbose_name="ID тега"
                    ),
                ),
            ],
            options={
                "verbose_name": "Тег",
                "verbose_name_plural": "Теги",
                "ordering": ["number"],
            },
        ),
        migrations.AlterModelOptions(
            name="pointtag",
            options={
                "ordering": ["id"],
                "verbose_name": "Тег КП",
                "verbose_name_plural": "Теги КП",
            },
        ),
        migrations.AlterField(
            model_name="pointtag",
            name="tag_id",
            field=models.CharField(max_length=255, verbose_name="ID тега"),
        ),
        migrations.AddField(
            model_name="pointtag",
            name="race",
            field=models.ForeignKey(
                default=1,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="kp_tag",
                to="website.race",
                verbose_name="Метки",
            ),
        ),
    ]