from django.db import migrations
from django.db.models import F


def populate_slug(apps, schema_editor):
    Race = apps.get_model("website", "Race")
    Race.objects.filter(slug="").update(slug=F("code"))


class Migration(migrations.Migration):
    dependencies = [("website", "0059_race_slug")]
    operations = [migrations.RunPython(populate_slug, migrations.RunPython.noop)]
