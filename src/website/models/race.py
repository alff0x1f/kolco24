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


class RegStatus(TextChoices):
    UPCOMING = "upcoming", "Откроется"  # регистрация ещё не стартовала
    OPEN = "open", "Открыта"  # регистрация открыта
    SOLD_OUT = "sold_out", "Мест нет"  # все слоты заняты


class Race(Model):
    name = CharField("Название", max_length=50)
    code = CharField("Код", max_length=15, unique=True)
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
            if value:
                try:
                    _url_validator(value)
                except ValidationError:
                    errors[field] = "Введите корректный URL (http/https)."
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

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.race})"


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
