from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("website", "0072_payment_vtb_payment"),
    ]

    operations = [
        migrations.DeleteModel(name="Transfer"),
        migrations.DeleteModel(name="BreakfastRegistration"),
    ]
