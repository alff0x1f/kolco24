# Generated by Django 2.2.15 on 2020-09-03 20:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0017_auto_20200902_1445'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='additional_charge',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='team',
            name='additional_charge',
            field=models.FloatField(default=0),
        ),
    ]