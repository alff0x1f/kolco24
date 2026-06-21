from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0089_checkpointtag_nfc_uid_unique"),
    ]

    operations = [
        migrations.RenameField(
            model_name="checkpointtag",
            old_name="point",
            new_name="checkpoint",
        ),
    ]
