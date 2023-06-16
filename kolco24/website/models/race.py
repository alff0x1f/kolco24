from django.db.models import BooleanField, CharField, DateField, Model
from django.utils import timezone


class Race(Model):
    name = CharField("Название", max_length=50)
    code = CharField("Код", max_length=15)
    date = DateField("Дата", default=timezone.now)
    is_active = BooleanField("Активна", default=True)
