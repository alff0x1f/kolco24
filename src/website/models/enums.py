from django.db.models import TextChoices


class CheckpointType(TextChoices):
    start = "start", "Старт"
    finish = "finish", "Финиш"
    test = "test", "Тест"
    kp = "kp", "КП"
