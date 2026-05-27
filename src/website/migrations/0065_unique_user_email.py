from django.db import migrations


def deduplicate_emails(apps, schema_editor):
    """Mark case-variant duplicate emails before creating the unique index.

    Keeps the oldest account (lowest pk) intact; replaces newer duplicates
    with a short pk-based placeholder that fits within auth_user.email
    max_length (254) and is unique by pk. Admins should review and clean up
    any renamed addresses after deploy.
    """
    User = apps.get_model("auth", "User")
    seen = {}
    for user in User.objects.exclude(email="").order_by("pk"):
        key = user.email.lower()
        if key in seen:
            placeholder = f"dup.{user.pk}@invalid.local"
            suffix = 0
            while User.objects.filter(email__iexact=placeholder).exists():
                suffix += 1
                placeholder = f"dup.{user.pk}.{suffix}@invalid.local"
            User.objects.filter(pk=user.pk).update(email=placeholder)
        else:
            seen[key] = user.pk


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0064_add_agree_news_to_profile"),
    ]

    operations = [
        migrations.RunPython(deduplicate_emails, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX IF NOT EXISTS website_user_email_unique"
                " ON auth_user (LOWER(email)) WHERE email != ''"
            ),
            reverse_sql="DROP INDEX IF EXISTS website_user_email_unique",
        ),
    ]
