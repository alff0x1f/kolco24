from django.db import models


class Tag(models.Model):
    number = models.IntegerField(verbose_name="Номер")
    tag_id = models.CharField(max_length=255, verbose_name="ID тега", unique=True)

    def __str__(self):
        return f"{self.id} - {self.tag_id}"

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"
        ordering = ["number"]


class PointTag(models.Model):
    race = models.ForeignKey(
        "Race",
        related_name="kp_tag",
        verbose_name="Метки",
        on_delete=models.CASCADE,
        default=1,
    )
    point = models.ForeignKey(
        "ControlPoint", verbose_name="КП", on_delete=models.CASCADE
    )
    tag_id = models.CharField(max_length=255, verbose_name="ID тега")

    def __str__(self):
        return f"{self.id} - {self.tag_id}"

    class Meta:
        verbose_name = "Тег КП"
        verbose_name_plural = "Теги КП"
        ordering = ["id"]
