from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.photo_contest.storage import private_storage


class PhotoContest(models.Model):
    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("Адрес (slug)", max_length=100, unique=True)
    description = models.TextField("Описание", blank=True)
    submission_until = models.DateTimeField("Приём фото до")
    max_photos_per_user = models.PositiveIntegerField(
        "Фото на участника в номинации", default=3
    )
    is_published = models.BooleanField(
        "Опубликован",
        default=False,
        help_text="Виден на сайте; до срока идёт приём фото.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submission_until"]
        verbose_name = "фотоконкурс"
        verbose_name_plural = "фотоконкурсы"

    def __str__(self):
        return self.title

    @property
    def is_submission_open(self):
        return self.is_published and timezone.now() <= self.submission_until


class PhotoNomination(models.Model):
    contest = models.ForeignKey(
        PhotoContest, on_delete=models.CASCADE, related_name="nominations"
    )
    title = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    order = models.IntegerField("Порядок", default=0)

    class Meta:
        ordering = ["order", "pk"]
        verbose_name = "номинация"
        verbose_name_plural = "номинации"

    def __str__(self):
        return self.title


class PhotoEntry(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS_CHOICES = [
        (PENDING, "На модерации"),
        (APPROVED, "Принято"),
        (REJECTED, "Отклонено"),
    ]

    # PROTECT: deleting a nomination (or its contest) must not silently drop
    # photos; entry files are laid out by nomination id, so entries are also
    # never moved between nominations.
    nomination = models.ForeignKey(
        PhotoNomination, on_delete=models.PROTECT, related_name="entries"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="photo_entries",
    )
    caption = models.CharField("Подпись", max_length=300, blank=True)
    original = models.FileField("Оригинал", storage=private_storage, max_length=255)
    preview = models.ImageField("Превью", max_length=255)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveIntegerField(default=0)
    status = models.CharField(
        "Статус", max_length=16, choices=STATUS_CHOICES, default=PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["nomination", "status"])]
        verbose_name = "фото"
        verbose_name_plural = "фото"

    def __str__(self):
        return self.caption or f"Фото #{self.pk}"
