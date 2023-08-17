from django.db.models import (
    CASCADE,
    BooleanField,
    CharField,
    DateField,
    ForeignKey,
    IntegerField,
    Manager,
    Model,
)
from django.utils import timezone


class Race(Model):
    name = CharField("Название", max_length=50)
    code = CharField("Код", max_length=15, unique=True)
    date = DateField("Дата", default=timezone.now)
    is_active = BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Гонка"
        verbose_name_plural = "Гонки"
        ordering = ["-date"]

    def __str__(self):
        return self.name


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
        return self.code
