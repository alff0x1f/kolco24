import datetime
import hashlib
import logging
import random

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.TextField(max_length=500, blank=True)


class Transfer(models.Model):
    people_count = models.PositiveIntegerField(
        verbose_name="Количество человек", default=1
    )
    passenger_contacts = models.JSONField(
        default=list,
        verbose_name="Участники",
        help_text="Список участников с их контактами",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    status = models.CharField(
        max_length=20,
        choices=[
            ("new", "Новая"),
            ("processed", "Обработана"),
            ("cancelled", "Отменена"),
        ],
        default="new",
        verbose_name="Статус",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Заявка на автобус"
        verbose_name_plural = "Заявки на автобус"

    def __str__(self) -> str:
        return f"{self.id} ({self.people_count})"


class BreakfastRegistration(models.Model):
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        related_name="breakfast_registrations",
        verbose_name="Гонка",
    )
    people_count = models.PositiveIntegerField(
        verbose_name="Количество человек", default=1
    )
    attendees = models.JSONField(
        default=list,
        verbose_name="Участники",
        help_text="Список участников и их предпочтений",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    status = models.CharField(
        max_length=20,
        choices=[
            ("new", "Новая"),
            ("processed", "Обработана"),
            ("cancelled", "Отменена"),
        ],
        default="new",
        verbose_name="Статус",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Заявка на завтрак"
        verbose_name_plural = "Заявки на завтрак"

    def __str__(self) -> str:
        return f"{self.race_id}: {self.people_count} участник(ов)"


class TeamStartLog(models.Model):
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        related_name="team_start_logs",
        verbose_name="Гонка",
    )
    team = models.ForeignKey(
        "website.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="start_logs",
        verbose_name="Команда",
    )
    start_number = models.CharField(
        max_length=50, blank=True, verbose_name="Стартовый номер"
    )
    team_name = models.CharField(
        max_length=255, blank=True, verbose_name="Название команды"
    )
    participant_count = models.PositiveIntegerField(
        default=0, verbose_name="Количество участников"
    )
    scanned_count = models.PositiveIntegerField(
        default=0, verbose_name="Сканировано браслетов"
    )
    member_tags = models.JSONField(
        default=list, blank=True, verbose_name="Теги участников"
    )
    start_timestamp = models.BigIntegerField(verbose_name="Время старта (мс)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Старт команды"
        verbose_name_plural = "Старты команд"

    def __str__(self) -> str:
        return f"{self.team_name or self.start_number} ({self.start_timestamp})"


class TeamFinishLog(models.Model):
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        related_name="team_finish_logs",
        verbose_name="Гонка",
    )
    team = models.ForeignKey(
        "website.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finish_logs",
        verbose_name="Команда",
    )
    member_tag_id = models.PositiveIntegerField(
        verbose_name="ID тега участника", null=True, blank=True
    )
    tag_uid = models.CharField(max_length=64, blank=True, verbose_name="UID тега")
    recorded_at = models.BigIntegerField(verbose_name="Время финиша (мс)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Финиш команды"
        verbose_name_plural = "Финиши команд"

    def __str__(self) -> str:
        return f"{self.tag_uid or self.member_tag_id} ({self.recorded_at})"


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
    sha1_hash = models.CharField(max_length=50)
    unaccepted = models.BooleanField(default=True)

    @staticmethod
    def get_cost():
        return 2500

    def new_payment(self, d: dict) -> bool:
        fields = [
            "notification_type",
            "operation_id",
            "amount",
            "currency",
            "datetime",
            "sender",
            "codepro",
            "label",
            "sha1_hash",
        ]
        for field in fields:
            if field not in d:
                return False

        notification_secret = settings.YANDEX_NOTIFICATION_SECRET

        s = "%s&%s&%s&%s&%s&%s&%s&%s&%s" % (
            d["notification_type"],
            d["operation_id"],
            d["amount"],
            d["currency"],
            d["datetime"],
            d["sender"],
            d["codepro"],
            notification_secret,
            d["label"],
        )

        hash = hashlib.sha1(s.encode("utf-8")).hexdigest()
        if d["sha1_hash"] != hash:
            logger.warning(f"sha1_hash not equal {d['sha1_hash']} != {hash}")
            return False

        self.notification_type = d["notification_type"]
        self.operation_id = d["operation_id"]
        self.amount = d["amount"]
        self.currency = d["currency"]
        self.datetime = d["datetime"]
        self.sender = d["sender"]
        self.codepro = d["codepro"] == "true"
        self.label = d["label"]
        self.sha1_hash = d["sha1_hash"]

        self.withdraw_amount = d.get("withdraw_amount", "0")
        if "unaccepted" in d:
            self.unaccepted = d["unaccepted"] == "true"

        self.save()
        if self.label:
            self.update_team(self.label)
        return True

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
        withdraw_amount = float(self.withdraw_amount) - payment.additional_charge
        paid_for = payment.paid_for
        if payment.payment_with_discount <= withdraw_amount:
            payment.status = "done"
        if payment.payment_with_discount > withdraw_amount:
            payment.status = "partial (" + str(withdraw_amount) + ")"
            paid_for = withdraw_amount / payment.cost_per_person
        if payment.team:
            payment.team.paid_people += paid_for
            payment.team.paid_sum += withdraw_amount + payment.additional_charge
            payment.team.additional_charge -= payment.additional_charge
            payment.team.save()
        payment.save()
        return True


class TeamManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Team(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    paymentid = models.CharField(max_length=50)
    paid_sum = models.FloatField(default=0)
    additional_charge = models.FloatField(default=0)
    paid_people = models.FloatField(default=0)
    dist = models.CharField(max_length=15)
    ucount = models.IntegerField(default=1)
    teamname = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=50, blank=True)
    organization = models.CharField(max_length=50, blank=True)
    year = models.IntegerField(default=23)

    map_count = models.IntegerField(default=0)
    map_count_paid = models.IntegerField(default=0)

    # ! athlet1-athlet6 deprecated, use Athlet model instead
    athlet1 = models.CharField(max_length=50, blank=True)
    birth1 = models.IntegerField(default=0)
    athlet2 = models.CharField(max_length=50, blank=True)
    birth2 = models.IntegerField(default=0)
    athlet3 = models.CharField(max_length=50, blank=True)
    birth3 = models.IntegerField(default=0)
    athlet4 = models.CharField(max_length=50, blank=True)
    birth4 = models.IntegerField(default=0)
    athlet5 = models.CharField(max_length=50, blank=True)
    birth5 = models.IntegerField(default=0)
    athlet6 = models.CharField(max_length=50, blank=True)
    birth6 = models.IntegerField(default=0)
    # ! end deprecated warning

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    get_package = models.BooleanField(default=False)
    get_number = models.BooleanField(default=False)
    get_map = models.BooleanField(default=False)
    give_paper = models.BooleanField(default=False)
    give_photos = models.BooleanField(default=False)

    # TODO: deprecated, use category2 instead
    category = models.CharField(max_length=50, default="", blank=True)
    category2 = models.ForeignKey(
        "Category",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        verbose_name="Категория",
    )
    start_number = models.CharField(max_length=50, default="", blank=True)
    start_time = models.BigIntegerField(default=0)
    finish_time = models.BigIntegerField(default=0)
    distance_time = models.DurationField(null=True, blank=True)
    penalty = models.IntegerField(default=0)
    dnf = models.BooleanField(default=False)
    points_sum = models.IntegerField(default=0)
    place = models.IntegerField(default=0)
    is_deleted = models.BooleanField(default=False)

    objects = TeamManager()
    all_objects = models.Manager()

    def __str__(self):  # __str__ on Python 3
        return f"id{self.id} - {self.start_number} {self.teamname}"

    @property
    def start_time_date(self):
        if not self.start_time:
            return
        return datetime.datetime.fromtimestamp(self.start_time / 1000)

    @property
    def finish_time_date(self):
        if not self.finish_time:
            return
        return datetime.datetime.fromtimestamp(self.finish_time / 1000)

    @property
    def has_payment_history(self):
        if not self.pk:
            return False
        return (
            Payment.objects.filter(team=self, status=Payment.STATUS_DONE).exists()
            or PaymentLog.objects.filter(team=self).exists()
        )

    @property
    def can_be_deleted(self):
        if not self.pk:
            return False
        return (
            not self.is_deleted
            and self.paid_people == 0
            and not self.has_payment_history
        )

    def new_team(self, user, dist, ucount):
        # print(user, dist)
        if user.is_authenticated:
            self.owner = user
            self.dist = dist
            self.ucount = ucount
            self.paymentid = "%016x" % random.randrange(16**16)
            self.year = settings.CURRENT_YEAR
            self.save()

    @staticmethod
    def get_info():
        teams = Team.objects.filter(paid_people__gt=0, year=settings.CURRENT_YEAR)
        people_paid = 0
        teams_count = 0
        teams_ids = set()
        for team in teams:
            people_paid += team.paid_people
            teams_ids.add(team.id)
            teams_count += 1
        time_15min_ago = datetime.datetime.now() - datetime.timedelta(minutes=15)
        payments = Payment.objects.filter(created_at__gte=time_15min_ago)
        for p in payments:
            teams_ids.add(p.team_id)
        return len(teams_ids), people_paid

    def update_points_sum(self):
        teams = Team.objects.filter(paid_sum__gt=0, year=settings.CURRENT_YEAR)
        for team in teams:
            points = TakenKP.objects.filter(team=team)
            points_sum = 0
            for point in points:
                points_sum += point.point.cost
            team.points_sum = points_sum - team.penalty
            team.save()

    def update_distance_time(self):
        teams = Team.objects.filter(paid_sum__gt=0, year=settings.CURRENT_YEAR)
        for team in teams:
            if team.start_time and team.finish_time:
                team.distance_time = team.finish_time - team.start_time
                team.save()
            else:
                if team.distance_time:
                    team.distance_time = None
                    team.save()

    def update_places(self):
        self.update_points_sum()
        self.update_distance_time()
        categories = ["6h", "12h_mm", "12h_mw", "12h_ww", "24h"]
        for category in categories:
            teams = Team.objects.filter(
                category=category,
                paid_sum__gt=0,
                year=settings.CURRENT_YEAR,
            ).order_by("-points_sum", "distance_time")
            place = 1
            for team in teams:
                if team.distance_time and team.points_sum:
                    team.place = place
                    team.save()
                    place += 1
                else:
                    if team.place != 10000:
                        team.place = 10000
                        team.save()
                if team.dnf and team.place != 10000:
                    team.place = 10000
                    team.save()


class TeamMemberMove(models.Model):
    from_team = models.ForeignKey(
        Team, related_name="moves_from", on_delete=models.CASCADE
    )
    to_team = models.ForeignKey(Team, related_name="moves_to", on_delete=models.CASCADE)
    moved_people = models.FloatField(default=0)
    move_date = models.DateTimeField(auto_now_add=True)

    def move_people(self):
        self.from_team.paid_people -= self.moved_people
        self.to_team.paid_people += self.moved_people

        with transaction.atomic():
            self.from_team.save(update_fields=["paid_people"])
            self.to_team.save(update_fields=["paid_people"])


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
    team = models.ForeignKey("Team", on_delete=models.CASCADE)
    payment_method = models.CharField(max_length=50)
    paid_sum = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Payment(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_DONE = "done"
    STATUS_DRAFT_WITH_INFO = "draft_with_info"
    STATUS_CANCEL = "cancel"

    STATUS_CHOICES = (
        (STATUS_DRAFT, "Черновик"),
        (STATUS_DONE, "Оплачено"),
        (STATUS_DRAFT_WITH_INFO, "Черновик с информацией"),
        (STATUS_CANCEL, "Отменено"),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    team = models.ForeignKey(
        "Team",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    recipient = models.ForeignKey(
        "SbpPaymentRecipient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    order = models.IntegerField(default=0)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50)
    payment_amount = models.FloatField(default=0)
    additional_charge = models.FloatField(default=0)
    payment_with_discount = models.FloatField(default=0)
    cost_per_person = models.FloatField(default=0)
    paid_for = models.FloatField(default=0)
    map = models.IntegerField(default=0)
    coupon = models.ForeignKey(
        "Coupons",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=50, default="draft", choices=STATUS_CHOICES)
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
            self.login_key = "%016x" % random.randrange(16**16)
            self.save()
            return self.login_key
        return False


class Athlet(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "Team",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=50)
    birth = models.IntegerField(default=0)
    number_in_team = models.IntegerField(default=0)
    paid = models.FloatField(default=0)

    def new_athlet(self, user, team, name, birth=-1):
        if user.is_authenticated:
            self.owner = user
            if team:
                self.team = team
            self.name = name
            if 1910 < birth < settings.CURRENT_YEAR:
                self.birth = birth
            self.save()


class Coupons(models.Model):
    COVER_TYPE_CHOICES = [("TEAM", "Coupon for team"), ("ATHLET", "Coupon for athlet")]
    code = models.CharField(max_length=20)
    expire_at = models.DateTimeField()
    discount_sum = models.FloatField(default=0)
    discount_persent = models.FloatField(default=0)
    cover_type = models.CharField(
        max_length=20, choices=COVER_TYPE_CHOICES, default="ATHLET"
    )
    count = models.IntegerField(default=1)
    avail_count = models.IntegerField(default=1)


class TakenKP(models.Model):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STATUS_CHOICES = (
        (NEW, "Новая"),
        (ACCEPTED, "Принята"),
        (REJECTED, "Отклонена"),
    )

    team = models.ForeignKey("Team", on_delete=models.CASCADE)
    point_number = models.IntegerField("Номер КП", default=0)
    image_url = models.CharField(max_length=200, default="")
    status = models.CharField(max_length=50, default="new", choices=STATUS_CHOICES)
    timestamp = models.BigIntegerField(default=0)
    nfc = models.CharField(max_length=300, default="")
    phone_uuid = models.CharField(max_length=100, default="")
    year = models.IntegerField(default=2024)
