from django.db import models


class Tag(models.Model):
    number = models.IntegerField(verbose_name="Номер")
    nfc_uid = models.CharField(max_length=255, verbose_name="UID тега")
    # null = legacy global pool (K24 v1 bracelets, reused across races); set for a
    # K24 v2 bracelet bound to one race by the judge's offline bind.
    race = models.ForeignKey(
        "website.Race",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="member_tags",
        verbose_name="Гонка",
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Последнее сканирование",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменён")
    # Secret 16-byte code written into the bracelet (K24 format, type 0x2).
    # Never exposed by any GET (mobile member_tags / api member_tag).
    code = models.BinaryField("Код браслета", null=True, blank=True)

    def save(self, *args, **kwargs):
        self.nfc_uid = (self.nfc_uid or "").strip().upper()
        if not self.nfc_uid:
            raise ValueError("nfc_uid must not be blank")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.id} - {self.nfc_uid}"

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["race", "nfc_uid"], name="tag_race_nfc_uid_unique"
            ),
            models.UniqueConstraint(
                fields=["nfc_uid"],
                condition=models.Q(race__isnull=True),
                name="tag_global_nfc_uid_unique",
            ),
        ]
