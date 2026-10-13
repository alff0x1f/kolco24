from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
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
    voting_until = models.DateTimeField(
        "Голосование до",
        null=True,
        blank=True,
        help_text="Идёт после приёма фото. Пусто — без голосования.",
    )
    max_comparisons_per_user = models.PositiveIntegerField(
        "Сравнений на участника в номинации",
        default=30,
        validators=[MinValueValidator(1)],
    )
    is_results_published = models.BooleanField("Итоги опубликованы", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submission_until"]
        verbose_name = "фотоконкурс"
        verbose_name_plural = "фотоконкурсы"

    def __str__(self):
        return self.title

    def clean(self):
        if (
            self.voting_until
            and self.submission_until
            and self.voting_until <= self.submission_until
        ):
            raise ValidationError(
                {"voting_until": "Голосование должно заканчиваться после приёма фото."}
            )

    @property
    def is_submission_open(self):
        return self.is_published and timezone.now() <= self.submission_until

    @property
    def is_voting_open(self):
        if not (self.is_published and self.voting_until):
            return False
        return self.submission_until < timezone.now() <= self.voting_until

    @property
    def is_voting_finished(self):
        return bool(self.voting_until) and timezone.now() > self.voting_until

    @property
    def phase(self):
        now = timezone.now()
        if now <= self.submission_until:
            return "submission"
        if not self.voting_until:
            return "no_voting"
        if now <= self.voting_until:
            return "voting"
        return "closed"

    @property
    def days_left(self):
        """Calendar days until the deadline in the project timezone; 0 = last day."""
        return (timezone.localdate(self.submission_until) - timezone.localdate()).days


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
    thumb = models.ImageField(
        "Миниатюра",
        max_length=255,
        blank=True,
        width_field="thumb_width",
        height_field="thumb_height",
    )
    thumb_width = models.PositiveIntegerField(null=True, blank=True)
    thumb_height = models.PositiveIntegerField(null=True, blank=True)
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

    @property
    def grid_image(self):
        """``(url, width, height)`` for a gallery tile; only the ratio matters."""
        if self.thumb:
            return self.thumb.url, self.thumb_width or 1, self.thumb_height or 1
        # Not preview.width: it has no width_field, so it would open the file.
        return self.preview.url, self.width or 1, self.height or 1


class PhotoContestModerator(models.Model):
    contest = models.ForeignKey(
        PhotoContest, on_delete=models.CASCADE, related_name="moderators"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moderated_photo_contests",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("contest", "user")
        ordering = ["created_at", "pk"]
        verbose_name = "администратор конкурса"
        verbose_name_plural = "администраторы конкурса"

    def __str__(self):
        return f"{self.user} — {self.contest}"


class Comparison(models.Model):
    """One pair shown to a voter; ``winner`` is null until answered."""

    nomination = models.ForeignKey(
        PhotoNomination, on_delete=models.CASCADE, related_name="comparisons"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="photo_comparisons",
    )
    # PROTECT: a photo that has been voted on can't vanish with its votes.
    left = models.ForeignKey(PhotoEntry, on_delete=models.PROTECT, related_name="+")
    right = models.ForeignKey(PhotoEntry, on_delete=models.PROTECT, related_name="+")
    winner = models.ForeignKey(
        PhotoEntry, on_delete=models.PROTECT, related_name="+", null=True
    )
    is_void = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True)
    ip = models.GenericIPAddressField(null=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=["nomination", "user"])]

    def __str__(self):
        return f"{self.left_id} vs {self.right_id}"
