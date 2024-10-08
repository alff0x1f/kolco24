# Generated by Django 3.2.15 on 2022-10-01 01:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0020_auto_20220807_2236"),
    ]

    operations = [
        migrations.AddField(
            model_name="controlpoint",
            name="description",
            field=models.CharField(
                default="", max_length=200, verbose_name="Описание КП"
            ),
        ),
        migrations.AlterField(
            model_name="controlpoint",
            name="number",
            field=models.IntegerField(
                default=1, verbose_name="Номер контрольной точки"
            ),
        ),
    ]
