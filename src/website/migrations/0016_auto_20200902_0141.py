# Generated by Django 2.2.14 on 2020-09-02 01:41

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0015_auto_20191011_2149"),
    ]

    operations = [
        migrations.AlterField(
            model_name="controlpoint",
            name="year",
            field=models.IntegerField(default=2020),
        ),
        migrations.AlterField(
            model_name="team",
            name="category",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AlterField(
            model_name="team",
            name="start_number",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AlterField(
            model_name="team",
            name="teamname",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name="team",
            name="year",
            field=models.IntegerField(default=2020),
        ),
    ]
