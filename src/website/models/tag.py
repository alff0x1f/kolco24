from django.db import models


class Tag(models.Model):
    number = models.IntegerField(verbose_name="Номер")
    tag_id = models.CharField(max_length=255, verbose_name="ID тега", unique=True)
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Последнее сканирование",
    )

    def __str__(self):
        return f"{self.id} - {self.tag_id}"

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"
        ordering = ["number"]
