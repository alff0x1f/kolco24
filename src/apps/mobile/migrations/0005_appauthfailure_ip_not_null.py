from django.db import migrations, models


class Migration(migrations.Migration):
    """Make AppAuthFailure.ip NOT NULL.

    The sentinel "0.0.0.0" (applied in SignedAppPermission._deny before any
    write) guarantees the column is never NULL in practice; null=True was an
    oversight.  Back-fill any accidental NULLs before altering the column.
    """

    dependencies = [
        ("mobile", "0004_appauthfailure"),
    ]

    operations = [
        migrations.RunSQL(
            sql="UPDATE mobile_appauthfailure SET ip = '0.0.0.0' WHERE ip IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="appauthfailure",
            name="ip",
            field=models.GenericIPAddressField(),
        ),
    ]
