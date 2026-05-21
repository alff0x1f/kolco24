from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("website", "0060_race_slug_populate")]

    operations = [
        migrations.AlterField(
            model_name="race",
            name="slug",
            field=models.SlugField(max_length=50, unique=True, verbose_name="URL-slug"),
        ),
    ]
