from django.apps import apps
from django.db.models import (
    CASCADE,
    BooleanField,
    CharField,
    DateField,
    ForeignKey,
    IntegerField,
    Manager,
    Model,
    TextChoices,
)
from django.utils import timezone


class RegStatus(TextChoices):
    UPCOMING = "upcoming", "Откроется"  # регистрация ещё не стартовала
    OPEN = "open", "Открыта"  # регистрация открыта
    SOLD_OUT = "sold_out", "Мест нет"  # все слоты заняты


class Race(Model):
    name = CharField("Название", max_length=50)
    code = CharField("Код", max_length=15, unique=True)
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

    def team_count(self):
        Team = apps.get_model("website", "Team")
        return len(Team.objects.filter(category2__race=self, paid_people__gt=0))

    def people_count(self):
        Team = apps.get_model("website", "Team")
        return sum(
            Team.objects.filter(category2__race=self).values_list(
                "paid_people", flat=True
            )
        )


class RaceLink(Model):
    name = CharField("Название", max_length=50)
    url = CharField("Ссылка", max_length=255)
    race = ForeignKey(
        "Race", related_name="links", verbose_name="Гонка", on_delete=CASCADE
    )

    def __str__(self):
        return f"{self.id} - {self.name} ({self.race})"

    class Meta:
        verbose_name = "Ссылка"
        verbose_name_plural = "Ссылки"


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

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.race})"
