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
    place = CharField("Место", max_length=50, default="")
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
    is_reg_open = BooleanField("Регистрация открыта", default=False)
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
