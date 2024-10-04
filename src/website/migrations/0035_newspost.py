# Generated by Django 3.2.25 on 2024-09-04 14:22

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0033_alter_team"),
    ]

    operations = [
        migrations.AddField(
            model_name="race",
            name="place",
            field=models.CharField(default="", max_length=50, verbose_name="Место"),
        ),
        migrations.AlterField(
            model_name="takenkp",
            name="year",
            field=models.IntegerField(default=2024),
        ),
        migrations.CreateModel(
            name="NewsPost",
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
                (
                    "title",
                    models.CharField(max_length=255, verbose_name="Заголовок новости"),
                ),
                (
                    "publication_date",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата публикации"
                    ),
                ),
                (
                    "content",
                    models.TextField(
                        help_text="Use Markdown format", verbose_name="Текст новости"
                    ),
                ),
                (
                    "content_html",
                    models.TextField(
                        editable=False,
                        help_text="Rendered HTML content",
                        verbose_name="Текст новости (HTML)",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "image",
                    models.ImageField(blank=True, null=True, upload_to="blog_images/"),
                ),
                (
                    "race",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="website.race",
                        verbose_name="Гонка",
                    ),
                ),
            ],
            options={
                "verbose_name": "Новость",
                "verbose_name_plural": "Новости",
                "ordering": ["-publication_date"],
            },
        ),
    ]