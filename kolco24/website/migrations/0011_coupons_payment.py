# Generated by Django 2.2.3 on 2019-07-09 04:53

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('website', '0010_auto_20190708_1317'),
    ]

    operations = [
        migrations.CreateModel(
            name='Coupons',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=20)),
                ('expire_at', models.DateTimeField()),
                ('discount_sum', models.FloatField(default=0)),
                ('discount_persent', models.FloatField(default=0)),
                ('cover_type', models.CharField(choices=[('TEAM', 'Coupon for team'), ('ATHLET', 'Coupon for athlet')], default='ATHLET', max_length=20)),
                ('count', models.IntegerField(default=1)),
                ('avail_count', models.IntegerField(default=1)),
            ],
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payment_method', models.CharField(max_length=50)),
                ('payment_amount', models.FloatField(default=0)),
                ('payment_with_discount', models.FloatField(default=0)),
                ('cost_per_person', models.FloatField(default=0)),
                ('paid_for', models.FloatField(default=0)),
                ('status', models.CharField(max_length=50)),
                ('sender_card_number', models.CharField(max_length=50)),
                ('payment_date', models.DateField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('athlet', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='website.Athlet')),
                ('coupon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='website.Coupons')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='website.Team')),
            ],
        ),
    ]
