# Generated by Django 2.2.14 on 2021-07-31 19:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0018_auto_20200903_2059'),
    ]

    operations = [
        migrations.AlterField(
            model_name='controlpoint',
            name='year',
            field=models.IntegerField(default=2021),
        ),
        migrations.AlterField(
            model_name='team',
            name='year',
            field=models.IntegerField(default=2021),
        ),
    ]
