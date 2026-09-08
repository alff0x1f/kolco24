import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("website", "0091_category_control_time_category_overtime_penalty"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="newspost",
            options={
                "ordering": ["-publication_date"],
                "verbose_name": "Публикация",
                "verbose_name_plural": "Публикации",
            },
        ),
        migrations.AddField(
            model_name="newspost",
            name="is_published",
            field=models.BooleanField(
                db_index=True, default=True, verbose_name="Опубликована"
            ),
        ),
        migrations.AddField(
            model_name="newspost",
            name="kind",
            field=models.CharField(
                choices=[("news", "Новость"), ("article", "Статья")],
                db_index=True,
                default="news",
                max_length=16,
                verbose_name="Тип",
            ),
        ),
        migrations.AddField(
            model_name="newspost",
            name="summary",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Короткий текст для карточки. "
                    "Если пусто, используется начало статьи."
                ),
                verbose_name="Анонс",
            ),
        ),
        migrations.AlterField(
            model_name="newspost",
            name="content",
            field=models.TextField(
                help_text="Use Markdown format", verbose_name="Текст"
            ),
        ),
        migrations.AlterField(
            model_name="newspost",
            name="content_html",
            field=models.TextField(
                editable=False,
                help_text="Rendered HTML content",
                verbose_name="Текст (HTML)",
            ),
        ),
        migrations.AlterField(
            model_name="newspost",
            name="publication_date",
            field=models.DateTimeField(
                db_index=True,
                default=django.utils.timezone.now,
                verbose_name="Дата публикации",
            ),
        ),
        migrations.AlterField(
            model_name="newspost",
            name="title",
            field=models.CharField(max_length=255, verbose_name="Заголовок"),
        ),
    ]
