# Generated by Django 3.2.19 on 2023-09-29 13:14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0029_add_point_tag"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pointtag",
            name="tag_id",
            field=models.CharField(max_length=255),
        ),
    ]
