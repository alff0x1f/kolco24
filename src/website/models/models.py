import datetime
import random

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.TextField(max_length=500, blank=True)


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


class TeamMemberRaceLog(models.Model):
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        related_name="member_race_logs",
        verbose_name="Гонка",
    )
    member_tag = models.ForeignKey(
        "website.Tag",
        on_delete=models.CASCADE,
        related_name="race_logs",
        verbose_name="Тег участника",
    )
    start_time = models.BigIntegerField(default=0, verbose_name="Время старта (мс)")
    finish_time = models.BigIntegerField(default=0, verbose_name="Время финиша (мс)")

    class Meta:
        unique_together = (("race", "member_tag"),)
        verbose_name = "Результат участника"
        verbose_name_plural = "Результаты участников"

    def __str__(self) -> str:
        return f"{self.member_tag_id} @ {self.race_id}"


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
            self.from_team.save(update_fields=["paid_people", "updated_at"])
            self.to_team.save(update_fields=["paid_people", "updated_at"])
            from .race import RegStatus

            category = self.to_team.category2
            race = category.race if category else None
            if (
                race
                and race.people_limit
                and race.reg_status == RegStatus.OPEN
                and race.people_count() >= race.people_limit
            ):
                race.reg_status = RegStatus.SOLD_OUT
                race.save(update_fields=["reg_status"])


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
    vtb_payment = models.OneToOneField(
        "VTBPayment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="race_payment",
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
    status = models.CharField(max_length=50, default="draft", choices=STATUS_CHOICES)
    sender_card_number = models.CharField(max_length=50)
    payment_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


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

    updated_at = models.DateTimeField(auto_now=True)

    def new_athlet(self, user, team, name, birth=-1):
        if user.is_authenticated:
            self.owner = user
            if team:
                self.team = team
            self.name = name
            if 1910 < birth < settings.CURRENT_YEAR:
                self.birth = birth
            self.save()


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
