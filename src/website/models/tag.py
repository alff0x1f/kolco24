from django.db import models


class Tag(models.Model):
    number = models.IntegerField(verbose_name="Номер")
    nfc_uid = models.CharField(max_length=255, verbose_name="UID тега", unique=True)
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
