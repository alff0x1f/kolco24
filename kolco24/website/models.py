from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.TextField(max_length=500, blank=True)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()


class Payments(models.Model):
    notification_type = models.CharField(max_length=20)
    operation_id = models.CharField(max_length=50)
    amount = models.CharField(max_length=10)
    withdraw_amount = models.CharField(max_length=10)
    currency = models.CharField(max_length=5)
    datetime = models.CharField(max_length=20)
    sender = models.CharField(max_length=50)
    codepro = models.BooleanField(default=False)
    label = models.CharField(max_length=50)
    sha1_hash = models.CharField(max_length=30)
    unaccepted = models.BooleanField(default=True)


class Team(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    paymentid = models.CharField(max_length=50)
    paid_sum = models.FloatField(default=0)
    paid_people = models.FloatField(default=0)
    dist = models.CharField(max_length=10)
    ucount = models.IntegerField(default=1)
    teamname = models.CharField(max_length=50)
    city = models.CharField(max_length=50)
    organization = models.CharField(max_length=50)
    athlet1 = models.CharField(max_length=50)
    birth1 = models.IntegerField(default=0)
    athlet2 = models.CharField(max_length=50)
    birth2 = models.IntegerField(default=0)
    athlet3 = models.CharField(max_length=50)
    birth3 = models.IntegerField(default=0)
    athlet4 = models.CharField(max_length=50)
    birth4 = models.IntegerField(default=0)
    athlet5 = models.CharField(max_length=50)
    birth5 = models.IntegerField(default=0)
    athlet6 = models.CharField(max_length=50)
    birth6 = models.IntegerField(default=0)

    def __str__(self):              # __str__ on Python 3
        return self.paymentid.__str__()
