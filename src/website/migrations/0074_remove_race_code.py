from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0073_delete_transfer_breakfast"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="race",
            name="code",
        ),
    ]
