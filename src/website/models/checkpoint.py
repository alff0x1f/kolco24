from django.db import models

from .enums import CheckpointColor, CheckpointType


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
    color = models.CharField(
        "Цвет КП",
        max_length=20,
        choices=CheckpointColor.choices,
        default="",
        blank=True,
    )
    is_legend_locked = models.BooleanField("Легенда заперта", default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"КП {self.number}-{self.cost} ({self.race})"

    class Meta:
        ordering = ["id"]
        verbose_name = "Контрольная точка"
        verbose_name_plural = "Контрольные точки"


class CheckpointSecret(models.Model):
    """Precomputed envelope-encryption secret for a locked checkpoint's legend.

    Exists **only** for locked КП. ``content_key`` is the random 32-byte AES key
    sealing the КП's ``{cost, description}`` (stored in ``enc_blob``). The DB is
    trusted in this threat model, so ``content_key`` is stored raw.
    """

    checkpoint = models.OneToOneField(
        "website.Checkpoint",
        verbose_name="КП",
        on_delete=models.CASCADE,
        related_name="secret",
    )
    content_key = models.BinaryField("Ключ контента")  # 32 raw bytes
    enc_blob = models.JSONField("Зашифрованная легенда")  # {"iv": b64, "ct": b64}
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Secret for {self.checkpoint_id}"

    class Meta:
        verbose_name = "Секрет КП"
        verbose_name_plural = "Секреты КП"


class CheckpointTag(models.Model):
    point = models.ForeignKey(
        "website.Checkpoint",
        verbose_name="КП",
        on_delete=models.CASCADE,
        related_name="tags",
    )
    nfc_uid = models.CharField(max_length=255, verbose_name="UID тега")
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
    code = models.BinaryField("Код тега", null=True, blank=True)  # 16 raw random bytes
    bid = models.CharField(
        "Идентификатор бандла", max_length=16, blank=True, default=""
    )  # sha256(code).hexdigest()[:16]
    bundle_blob = models.JSONField(
        "Бандл", null=True, blank=True
    )  # {"iv": b64, "ct": b64}
    unlocks = models.ManyToManyField(
        "website.Checkpoint",
        verbose_name="Отпирает КП",
        related_name="unlocked_by",
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.nfc_uid = (self.nfc_uid or "").strip().upper()
        if not self.nfc_uid:
            raise ValueError("nfc_uid must not be blank")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.id} - {self.nfc_uid}"

    class Meta:
        verbose_name = "Тег КП"
        verbose_name_plural = "Теги КП"
        ordering = ["id"]
        unique_together = [("point", "nfc_uid")]
