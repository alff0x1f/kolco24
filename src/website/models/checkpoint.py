from django.db import models

from .enums import CheckpointType


class Checkpoint(models.Model):
    race = models.ForeignKey(
        "Race", verbose_name="Гонка", on_delete=models.SET_NULL, blank=True, null=True
    )
    number = models.IntegerField("Номер контрольной точки", default=1)
    cost = models.IntegerField("Стоимость", default=1)
    description = models.CharField("Описание КП", max_length=200, default="")
    year = models.IntegerField("Год", default=2024)  # deprecated
    iterator = models.IntegerField(default=0)  # for export
    type = models.CharField(
        "Тип точки",
        max_length=50,
        choices=CheckpointType.choices,
        default="kp",
    )

    def __str__(self):
        return f"КП {self.number}-{self.cost} ({self.race})"

    class Meta:
        ordering = ["id"]
        verbose_name = "Контрольная точка"
        verbose_name_plural = "Контрольные точки"


class CheckpointTag(models.Model):
    point = models.ForeignKey(
        "website.Checkpoint", verbose_name="КП", on_delete=models.CASCADE
    )
    tag_id = models.CharField(max_length=255, verbose_name="ID тега")
    check_method = models.CharField(
        "Метод проверки",
        max_length=20,
        choices=[
            ("offline", "Offline"),
            ("online", "Online"),
            ("local_server", "Local Server"),
        ],
        default="offline",
    )

    def __str__(self):
        return f"{self.id} - {self.tag_id}"

    class Meta:
        verbose_name = "Тег КП"
        verbose_name_plural = "Теги КП"
        ordering = ["id"]
