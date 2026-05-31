from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("website", "0068_racepricetier"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="race",
            name="is_reg_open",
        ),
    ]
