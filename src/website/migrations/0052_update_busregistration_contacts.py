from django.db import migrations, models


def migrate_passengers_to_contacts(apps, schema_editor):
    BusRegistration = apps.get_model("website", "BusRegistration")
    for registration in BusRegistration.objects.all():
        raw_passengers = getattr(registration, "passengers", "") or ""
        contacts = []
        for line in raw_passengers.splitlines():
            cleaned = line.strip()
            if cleaned:
                contacts.append({"name": cleaned, "phone": ""})
        if not contacts and raw_passengers.strip():
            contacts = [{"name": raw_passengers.strip(), "phone": ""}]
        registration.passenger_contacts = contacts
        registration.save(update_fields=["passenger_contacts"])


class Migration(migrations.Migration):
    dependencies = [
        ("website", "0051_busregistration"),
    ]

    operations = [
        migrations.AddField(
            model_name="busregistration",
            name="passenger_contacts",
            field=models.JSONField(
                default=list,
                help_text="Список участников с их контактами",
                verbose_name="Участники",
            ),
        ),
        migrations.RunPython(migrate_passengers_to_contacts, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="busregistration",
            name="passengers",
        ),
    ]
