# Generated by Django 2.2.6 on 2019-10-10 07:29

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0012_auto_20190722_0056'),
    ]

    operations = [
        migrations.CreateModel(
            name='ControlPoint',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(max_length=10)),
                ('cost', models.IntegerField(default=0)),
                ('year', models.IntegerField(default=2019)),
            ],
        ),
        migrations.CreateModel(
            name='TakenKP',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('point', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='website.ControlPoint')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='website.Team')),
            ],
        ),
    ]
