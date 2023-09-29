from django.db import models


class PointTag(models.Model):
    point = models.ForeignKey(
        "ControlPoint", verbose_name="КП", on_delete=models.CASCADE
    )
    tag_id = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.id} - {self.tag_id}"

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"
        ordering = ["id"]
