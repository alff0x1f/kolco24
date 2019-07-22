import datetime
import random
import time
import hashlib
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


class PaymentsYa(models.Model):
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

    def get_cost(self, t=0):
        s_format = "%d.%m.%Y %H:%M"
        cost_by_date = [
            ("10.10.2019 19:00", 1000),
            ("30.09.2019 19:00", 900),
            ("31.08.2019 19:00", 800),
            ("18.08.2019 19:00", 700),
            ("31.07.2019 19:00", 600),
        ]
        if not t:
            t = time.time()
        cost = 1000
        for d in cost_by_date:
            datestamp = datetime.datetime.strptime(
                d[0], s_format).timestamp()-time.timezone
            if t < datestamp:
                cost = d[1]
        teams_count, members_count = Team().get_info()
        if teams_count < 15:
            cost = 500
        return cost

    def new_payment(self, d):
        fields = [
            "notification_type", "operation_id", "amount", "currency",
            "datetime", "sender", "codepro", "label", "sha1_hash",
            "withdraw_amount"
        ]
        for field in fields:
            if field not in d:
                return False

        notification_secret = settings.YANDEX_NOTIFICATION_SECRET

        s = "%s&%s&%s&%s&%s&%s&%s&%s&%s" % (d['notification_type'],
                                            d['operation_id'], d['amount'],
                                            d['currency'], d['datetime'],
                                            d['sender'], d['codepro'],
                                            notification_secret, d['label'])

        hash = hashlib.sha1(s.encode('utf-8')).hexdigest()
        if d['sha1_hash'] == hash:
            self.notification_type = d['notification_type']
            self.operation_id = d['operation_id']
            self.amount = d['amount']
            self.currency = d['currency']
            self.datetime = d['datetime']
            self.sender = d['sender']
            if d['codepro'] == "false":
                self.codepro = False
            if d['codepro'] == "true":
                self.codepro = True
            self.label = d['label']
            self.sha1_hash = d['sha1_hash']

            self.withdraw_amount = d["withdraw_amount"]
            if "unaccepted" in d:
                if d["unaccepted"] == "false":
                    self.unaccepted = False
                if d["unaccepted"] == "true":
                    self.unaccepted = True

            self.save()
            self.update_team(self.label)
            return True
        return False

    def get_sum(self, paymentid):
        payments = PaymentsYa.objects.filter(label=paymentid, unaccepted=False)
        paid = 0
        for payment in payments:
            amount = float(payment.amount) if payment.amount else 0
            paid += amount
        return paid

    def update_team(self, paymentid):
        payment = Payment.objects.filter(id=int(paymentid))[:1]
        if not payment:
            return False
        payment = payment.get()
        if payment.payment_with_discount <= self.withdraw_amount:
            payment.status = "done"
        if payment.payment_with_discount > self.withdraw_amount:
            payment.stats = "partial"
        if payment.team:
            payment.team.ucount = payment.paid_for
            payment.team.paid_sum = payment.payment_with_discount
            payment.team.save()
        payment.save()
        return True


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
    year = models.IntegerField(default=2018)

    #! athlet1-athlet6 deprecated, use Athlet model instead
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
    #! end deprecated warning

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    get_package = models.BooleanField(default=False)
    get_number = models.BooleanField(default=False)
    get_map = models.BooleanField(default=False)
    give_paper = models.BooleanField(default=False)
    give_photos = models.BooleanField(default=False)
    category = models.CharField(max_length=50, default="")
    start_number = models.CharField(max_length=50, default="")
    start_time = models.DateTimeField(null=True, blank=True)
    finish_time = models.DateTimeField(null=True, blank=True)
    distance_time = models.DurationField(null=True, blank=True)
    penalty = models.IntegerField(default=0)
    dnf = models.BooleanField(default=False)

    def __str__(self):              # __str__ on Python 3
        return self.paymentid.__str__()

    def new_team(self, user, dist, ucount):
        # print(user, dist)
        if user.is_authenticated:
            self.owner = user
            self.dist = dist
            self.ucount = ucount
            self.paymentid = '%016x' % random.randrange(16**16)
            self.year = 2019
            self.save()

    def get_info(self):
        teams = Team.objects.filter(paid_sum__gt=0, year=2019)
        people_paid = 0
        teams_count = 0
        for team in teams:
            people_paid += team.paid_people
            teams_count += 1
        return (teams_count, people_paid)


class TeamAdminLog(models.Model):
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    paymentid = models.CharField(max_length=50)
    get_package = models.BooleanField(default=False)
    get_number = models.BooleanField(default=False)
    get_map = models.BooleanField(default=False)
    give_paper = models.BooleanField(default=False)
    give_photos = models.BooleanField(default=False)
    category = models.CharField(max_length=50, default="")
    start_number = models.CharField(max_length=50, default="")
    start_time = models.DateTimeField(null=True, blank=True)
    finish_time = models.DateTimeField(null=True, blank=True)
    distance_time = models.DurationField(null=True, blank=True)
    penalty = models.IntegerField(default=0)
    dnf = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class PaymentLog(models.Model):
    team = models.ForeignKey('Team', on_delete=models.CASCADE)
    payment_method = models.CharField(max_length=50)
    paid_sum = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Payment(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    team = models.ForeignKey(
        'Team',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    athlet = models.ForeignKey(
        'Athlet',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    payment_method = models.CharField(max_length=50)
    payment_amount = models.FloatField(default=0)
    payment_with_discount = models.FloatField(default=0)
    cost_per_person = models.FloatField(default=0)
    paid_for = models.FloatField(default=0)
    coupon = models.ForeignKey(
        'Coupons',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=50)
    sender_card_number = models.CharField(max_length=50)
    payment_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FastLogin(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    login_key = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    def new_login_link(self, email):
        u = User.objects.filter(email__iexact=email)
        if u[:1]:
            u = u[0]
            self.user = u
            self.login_key = '%016x' % random.randrange(16**16)
            self.save()
            return self.login_key
        return False


class Athlet(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        'Team',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=50)
    birth = models.IntegerField(default=0)
    number_in_team = models.IntegerField(default=0)
    paid = models.FloatField(default=0)

    def new_athlet(self, user, team, name, birth = -1):
        if user.is_authenticated:
            self.owner = user
            if team:
                self.team = team
            self.name = name
            if 1910 < birth < 2020:
                self.birth = birth
            self.save()


class Coupons(models.Model):
    COVER_TYPE_CHOICES = [
        ('TEAM', 'Coupon for team'),
        ('ATHLET', 'Coupon for athlet')
    ]
    code = models.CharField(max_length=20)
    expire_at = models.DateTimeField()
    discount_sum = models.FloatField(default=0)
    discount_persent = models.FloatField(default=0)
    cover_type = models.CharField(
        max_length=20,
        choices=COVER_TYPE_CHOICES,
        default='ATHLET'
    )
    count = models.IntegerField(default=1)
    avail_count = models.IntegerField(default=1)
