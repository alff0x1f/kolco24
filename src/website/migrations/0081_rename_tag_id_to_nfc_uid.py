from django.db import migrations, models


def uppercase_nfc_uids(apps, schema_editor):
    CheckpointTag = apps.get_model("website", "CheckpointTag")
    Tag = apps.get_model("website", "Tag")

    # CheckpointTag has updated_at (auto_now) — touch it so the mobile legend
    # fingerprint / ETag moves after the casing change.
    for tag in CheckpointTag.objects.all():
        normalized = (tag.nfc_uid or "").strip().upper()
        if normalized != tag.nfc_uid:
            tag.nfc_uid = normalized
            tag.save(update_fields=["nfc_uid", "updated_at"])

    # Tag.nfc_uid is unique=True — normalizing (strip+uppercase) could collide
    # two rows that differ only by case or surrounding whitespace. Abort with a
    # clear error so a human resolves the duplicate first (do NOT silently
    # merge/drop).
    seen = {}
    collisions = []
    for pk, nfc_uid in Tag.objects.values_list("pk", "nfc_uid"):
        normalized = (nfc_uid or "").strip().upper()
        if normalized in seen:
            collisions.append((seen[normalized], pk, normalized))
        else:
            seen[normalized] = pk
    if collisions:
        details = ", ".join(
            f"rows {a} and {b} both become '{u}'" for a, b, u in collisions
        )
        raise RuntimeError(
            "Cannot normalize Tag.nfc_uid: duplicates exist after strip+uppercase "
            f"({details}). Resolve these duplicates manually before migrating."
        )

    for tag in Tag.objects.all():
        normalized = (tag.nfc_uid or "").strip().upper()
        if normalized != tag.nfc_uid:
            tag.nfc_uid = normalized
            tag.save(update_fields=["nfc_uid"])


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0080_checkpointtag_updated_at"),
    ]

    operations = [
        migrations.RenameField(
            model_name="checkpointtag",
            old_name="tag_id",
            new_name="nfc_uid",
        ),
        migrations.RenameField(
            model_name="tag",
            old_name="tag_id",
            new_name="nfc_uid",
        ),
        migrations.AlterField(
            model_name="checkpointtag",
            name="nfc_uid",
            field=models.CharField(max_length=255, verbose_name="UID тега"),
        ),
        migrations.AlterField(
            model_name="tag",
            name="nfc_uid",
            field=models.CharField(
                max_length=255, unique=True, verbose_name="UID тега"
            ),
        ),
        migrations.RunPython(uppercase_nfc_uids, migrations.RunPython.noop),
    ]
