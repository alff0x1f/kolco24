from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import (
    CASCADE,
    BooleanField,
    CharField,
    DateField,
    ForeignKey,
    IntegerField,
    Manager,
    Model,
    SlugField,
    Sum,
    TextChoices,
)
from django.utils import timezone

_url_validator = URLValidator(schemes=["http", "https"])

# «Живая бронь» места: команда создала draft-платёж (ушла на оплату), но ещё не
# подтверждена. Такой черновик держит её места занятыми в течение этого окна,
# после чего слот освобождается для других (см. ``reserved_people``). Перекрывает
# реальное окно оплаты VTB (~5–10 мин) с запасом; fail-safe — расхождение TTL и
# фактического срока заказа ограничено этим интервалом.
RESERVATION_TTL = timedelta(minutes=20)


class RegStatus(TextChoices):
    UPCOMING = "upcoming", "Откроется"  # регистрация ещё не стартовала
    OPEN = "open", "Открыта"  # регистрация открыта
    SOLD_OUT = "sold_out", "Мест нет"  # все слоты заняты


class Race(Model):
    name = CharField("Название", max_length=50)
    slug = SlugField("URL-slug", max_length=50, unique=True)
    date = DateField("Дата", default=timezone.now)
    date_end = DateField("Дата окончания", default=timezone.now)
    place = CharField("Место", max_length=50, blank=True, default="")
    is_active = BooleanField("Активна", default=True)
    cost = IntegerField("Стоимость участия", default=0)

    reg_status = CharField(
        "Статус регистрации",
        max_length=16,
        choices=RegStatus.choices,
        default=RegStatus.UPCOMING,
        db_index=True,
    )

    header_image = CharField("Картинка в шапке", max_length=255, blank=True, default="")
    header_logo = CharField("Логотип в шапке", max_length=255, blank=True, default="")

    is_legend_visible = BooleanField("Легенда открыта", default=False)
    is_teams_editable = BooleanField("Команды редактируемы", default=False)
    is_photo_upload_enabled = BooleanField("Загрузка фото включена", default=False)

    people_limit = IntegerField("Лимит участников", default=0)  # 0 = без лимита

    class Meta:
        verbose_name = "Гонка"
        verbose_name_plural = "Гонки"
        ordering = ["-date"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        for field in ("header_image", "header_logo"):
            value = getattr(self, field)
            # Root-relative paths (e.g. /static/images/cover.jpg) are rendered
            # directly into <img src> and are valid; only full URLs are
            # validated against the http/https scheme.
            if value and not value.startswith("/"):
                try:
                    _url_validator(value)
                except ValidationError:
                    errors[field] = (
                        "Введите корректный URL (http/https) "
                        "или путь от корня (/static/…)."
                    )
        if errors:
            raise ValidationError(errors)

    def team_count(self):
        Team = apps.get_model("website", "Team")
        return Team.objects.filter(category2__race=self, paid_people__gt=0).count()

    def people_count(self):
        Team = apps.get_model("website", "Team")
        result = Team.objects.filter(category2__race=self).aggregate(
            total=Sum("paid_people")
        )["total"]
        return result or 0

    def reserved_people(self, exclude_team=None):
        """Σ забронированных, но ещё не оплаченных мест гонки.

        «Живая бронь» команды — наличие ``Payment`` со ``status="draft"``,
        созданного не раньше ``RESERVATION_TTL`` назад (команда ушла на оплату,
        но ещё не подтверждена). Считается по командам (``distinct`` — повторный
        сабмит не множит бронь); для каждой резервируется
        ``max(0, ucount − paid_people)`` — места, которые платёж добьёт до
        полного состава. ``exclude_team`` само-исключается.
        """
        Team = apps.get_model("website", "Team")
        Payment = apps.get_model("website", "Payment")
        cutoff = timezone.now() - RESERVATION_TTL
        teams = (
            Team.objects.filter(
                category2__race=self,
                payment__status=Payment.STATUS_DRAFT,
                payment__created_at__gt=cutoff,
            )
            .distinct()
            .only("id", "ucount", "paid_people")
        )
        reserved = 0
        for team in teams:
            if exclude_team is not None and team.id == exclude_team.id:
                continue
            reserved += max(0, int(team.ucount) - team.paid_people)
        return reserved

    def remaining_people(self, exclude_team=None):
        """Свободные слоты гонки или ``None`` при отсутствии лимита.

        Занятость = оплаченные (``people_count``) + забронированные
        (``reserved_people`` — живые draft-платежи). ``exclude_team`` вычитается
        из обеих составляющих (само-исключение при редактировании команды).
        """
        if not self.people_limit:  # 0 → без лимита
            return None
        occupied = self.people_count()
        if exclude_team is not None:
            occupied -= exclude_team.paid_people
        reserved = self.reserved_people(exclude_team=exclude_team)
        return max(0, self.people_limit - occupied - reserved)

    def _active_tier_index(self, tiers):
        """Index of the active tier within ``tiers`` (assumed ordered).

        Active tier = earliest with ``active_until >= today``; if all are
        past, the last tier is treated as active. Returns ``None`` for an
        empty ladder.
        """
        if not tiers:
            return None
        today = timezone.localdate()
        for index, tier in enumerate(tiers):
            if tier.active_until >= today:
                return index
        return len(tiers) - 1

    @property
    def current_price(self):
        """Single source of truth for the charged per-person price.

        Returns the active tier's price, falling back to ``self.cost`` when
        the race has no price tiers.
        """
        tiers = list(self.price_tiers.all())
        index = self._active_tier_index(tiers)
        if index is None:
            return self.cost
        return tiers[index].price

    def price_tier_ladder(self):
        """Return ``[{"tier": t, "status": "past|active|future"}]`` for display."""
        tiers = list(self.price_tiers.all())
        active_index = self._active_tier_index(tiers)
        if active_index is None:
            return []
        ladder = []
        for index, tier in enumerate(tiers):
            if index < active_index:
                status = "past"
            elif index == active_index:
                status = "active"
            else:
                status = "future"
            ladder.append({"tier": tier, "status": status})
        return ladder


class RaceLink(Model):
    name = CharField("Название", max_length=50)
    url = CharField("Ссылка", max_length=255)
    race = ForeignKey(
        "Race", related_name="links", verbose_name="Гонка", on_delete=CASCADE
    )

    def clean(self):
        super().clean()
        try:
            _url_validator(self.url)
        except ValidationError:
            raise ValidationError({"url": "Введите корректный URL (http/https)."})

    def __str__(self):
        return f"{self.id} - {self.name} ({self.race})"

    class Meta:
        verbose_name = "Ссылка"
        verbose_name_plural = "Ссылки"


class RaceAdmin(Model):
    class Role(TextChoices):
        ADMIN = "admin", "Администратор"
        MODERATOR = "moderator", "Модератор"

    race = ForeignKey(Race, on_delete=CASCADE, related_name="race_admins")
    user = ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name="race_admins"
    )
    role = CharField(max_length=16, choices=Role.choices, default=Role.ADMIN)

    class Meta:
        unique_together = ("race", "user")
        verbose_name = "Администратор гонки"
        verbose_name_plural = "Администраторы гонки"

    def __str__(self):
        return f"{self.user} — {self.race} ({self.role})"


class ActiveManager(Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Category(Model):
    objects = Manager()
    active_objects = ActiveManager()

    code = CharField("Код", max_length=15)
    short_name = CharField("Короткое название", default="", max_length=15)
    name = CharField("Название", max_length=50)
    description = CharField("Описание", max_length=150, blank=True)
    race = ForeignKey("Race", verbose_name="Гонка", on_delete=CASCADE)
    is_active = BooleanField("Активна", default=True)
    order = IntegerField("Порядок", default=0)
    min_people = IntegerField("Минимум участников", default=2)
    max_people = IntegerField("Максимум участников", default=6)
    people_limit = IntegerField("Лимит участников", default=0)  # 0 = без лимита

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.race})"

    def people_count(self):
        Team = apps.get_model("website", "Team")
        result = Team.objects.filter(category2=self).aggregate(
            total=Sum("paid_people")
        )["total"]
        return result or 0

    def reserved_people(self, exclude_team=None):
        """Σ забронированных, но ещё не оплаченных мест категории.

        Зеркало ``Race.reserved_people`` в разрезе категории: живые draft-платежи
        команд этой категории, ``max(0, ucount − paid_people)`` на команду,
        ``distinct`` по командам, ``exclude_team`` само-исключается.
        """
        Team = apps.get_model("website", "Team")
        Payment = apps.get_model("website", "Payment")
        cutoff = timezone.now() - RESERVATION_TTL
        teams = (
            Team.objects.filter(
                category2=self,
                payment__status=Payment.STATUS_DRAFT,
                payment__created_at__gt=cutoff,
            )
            .distinct()
            .only("id", "ucount", "paid_people")
        )
        reserved = 0
        for team in teams:
            if exclude_team is not None and team.id == exclude_team.id:
                continue
            reserved += max(0, int(team.ucount) - team.paid_people)
        return reserved

    def remaining_people(self, exclude_team=None):
        """Свободные слоты категории или ``None`` при отсутствии лимита.

        Занятость = оплаченные (``people_count``) + забронированные
        (``reserved_people`` — живые draft-платежи). ``exclude_team`` вычитается
        из обеих составляющих (само-исключение при редактировании команды, уже
        состоящей в этой категории).
        """
        if not self.people_limit:  # 0 → без лимита
            return None
        occupied = self.people_count()
        if exclude_team and exclude_team.category2_id == self.id:
            occupied -= exclude_team.paid_people
        reserved = self.reserved_people(exclude_team=exclude_team)
        return max(0, self.people_limit - occupied - reserved)


class RacePriceTier(Model):
    race = ForeignKey(
        "Race",
        related_name="price_tiers",
        verbose_name="Гонка",
        on_delete=CASCADE,
    )
    price = IntegerField("Цена за человека")
    active_until = DateField("Действует по (включительно)")
    order = IntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Ценовой период"
        verbose_name_plural = "Ценовые периоды"
        ordering = ["active_until", "order"]

    def __str__(self):
        return f"{self.race}: {self.price}₽ до {self.active_until}"
