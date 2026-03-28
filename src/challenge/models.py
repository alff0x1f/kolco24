from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class TelegramChat(models.Model):
    telegram_id = models.BigIntegerField(unique=True, verbose_name="ID чата в Telegram")
    name = models.CharField(max_length=255, verbose_name="Название")
    chat_type = models.CharField(max_length=50, verbose_name="Тип чата")
    source_file = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Файл экспорта",
        help_text="Исходный JSON-файл, из которого загружен чат.",
    )
    raw = models.JSONField(default=dict, blank=True, verbose_name="Сырые данные чата")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "telegram_id")
        verbose_name = "Telegram чат"
        verbose_name_plural = "Telegram чаты"

    def __str__(self):
        return self.name


class TelegramMessage(models.Model):
    chat = models.ForeignKey(
        TelegramChat,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Чат",
    )
    telegram_id = models.BigIntegerField(verbose_name="ID сообщения в Telegram")
    message_type = models.CharField(max_length=32, verbose_name="Тип сообщения")
    sent_at = models.DateTimeField(verbose_name="Дата сообщения")
    edited_at = models.DateTimeField(blank=True, null=True, verbose_name="Дата редактирования")
    sender_name = models.CharField(max_length=255, blank=True, verbose_name="Отправитель")
    sender_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ID отправителя",
    )
    actor_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Исполнитель сервисного действия",
    )
    actor_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ID исполнителя сервисного действия",
    )
    action = models.CharField(max_length=64, blank=True, verbose_name="Сервисное действие")
    inviter = models.CharField(max_length=255, blank=True, verbose_name="Кто пригласил")
    reply_to_message_id = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="ID сообщения, на которое это ответ",
    )
    forwarded_from = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Источник пересланного сообщения",
    )
    forwarded_from_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ID источника пересланного сообщения",
    )
    via_bot = models.CharField(max_length=255, blank=True, verbose_name="Бот-источник")
    text = models.TextField(blank=True, verbose_name="Текст сообщения")
    raw_text = models.JSONField(blank=True, null=True, verbose_name="Исходное поле text")
    text_entities = models.JSONField(default=list, blank=True, verbose_name="Разметка текста")
    reactions = models.JSONField(default=list, blank=True, verbose_name="Реакции")
    media_type = models.CharField(max_length=64, blank=True, verbose_name="Тип медиа")
    photo = models.CharField(max_length=255, blank=True, verbose_name="Фото")
    file = models.CharField(max_length=255, blank=True, verbose_name="Файл")
    file_name = models.CharField(max_length=255, blank=True, verbose_name="Имя файла")
    mime_type = models.CharField(max_length=255, blank=True, verbose_name="MIME-тип")
    thumbnail = models.CharField(max_length=255, blank=True, verbose_name="Миниатюра")
    title = models.CharField(max_length=255, blank=True, verbose_name="Заголовок")
    performer = models.CharField(max_length=255, blank=True, verbose_name="Исполнитель")
    sticker_emoji = models.CharField(max_length=32, blank=True, verbose_name="Emoji стикера")
    duration_seconds = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Длительность, сек.",
    )
    width = models.PositiveIntegerField(blank=True, null=True, verbose_name="Ширина")
    height = models.PositiveIntegerField(blank=True, null=True, verbose_name="Высота")
    photo_file_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Размер фото, байт",
    )
    file_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Размер файла, байт",
    )
    thumbnail_file_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Размер миниатюры, байт",
    )
    live_location_period_seconds = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Период live location, сек.",
    )
    media_spoiler = models.BooleanField(default=False, verbose_name="Медиа под спойлером")
    members = models.JSONField(default=list, blank=True, verbose_name="Участники")
    poll = models.JSONField(blank=True, null=True, verbose_name="Опрос")
    inline_bot_buttons = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Inline кнопки бота",
    )
    location_information = models.JSONField(
        blank=True,
        null=True,
        verbose_name="Информация о локации",
    )
    raw = models.JSONField(default=dict, blank=True, verbose_name="Сырые данные сообщения")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sent_at", "telegram_id")
        verbose_name = "Telegram сообщение"
        verbose_name_plural = "Telegram сообщения"
        constraints = [
            models.UniqueConstraint(
                fields=("chat", "telegram_id"),
                name="challenge_chat_message_unique",
            )
        ]
        indexes = [
            models.Index(fields=("chat", "sent_at"), name="challenge_msg_chat_sent_idx"),
            models.Index(fields=("message_type",), name="challenge_msg_type_idx"),
            models.Index(fields=("sender_id",), name="challenge_msg_sender_idx"),
        ]

    def __str__(self):
        author = self.sender_name or self.actor_name or "System"
        return f"{author}: {self.text[:60] or self.message_type}"


class Challenge(models.Model):
    name = models.CharField(max_length=255, verbose_name="Название")
    start_date = models.DateField(verbose_name="Дата начала")
    end_date = models.DateField(verbose_name="Дата окончания")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-start_date", "name")
        verbose_name = "Челлендж"
        verbose_name_plural = "Челленджи"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError(
                {"end_date": "Дата окончания должна быть не раньше даты начала."}
            )


class ChallengeParticipant(models.Model):
    challenge = models.ForeignKey(
        Challenge,
        on_delete=models.CASCADE,
        related_name="participants",
        verbose_name="Челлендж",
    )
    telegram_user_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ID участника в Telegram",
    )
    display_name = models.CharField(max_length=255, verbose_name="Имя участника")
    group = models.CharField(max_length=255, blank=True, verbose_name="Группа")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name", "id")
        verbose_name = "Участник челленджа"
        verbose_name_plural = "Участники челленджа"
        constraints = [
            models.UniqueConstraint(
                fields=("challenge", "telegram_user_id"),
                condition=~Q(telegram_user_id=""),
                name="challenge_participant_telegram_id_unique",
            )
        ]

    def __str__(self):
        return self.display_name

    @property
    def total_points(self):
        return sum(activity.total_points for activity in self.activities.all())


class ChallengeActivity(models.Model):
    TYPE_RUN = "run"
    TYPE_SKI = "ski"
    TYPE_BIKE = "bike"
    TYPE_SWIM = "swim"
    TYPE_HIKE_DAY = "hike_day"
    TYPE_GSH_DAY = "gsh_day"

    ACTIVITY_TYPE_CHOICES = (
        (TYPE_RUN, "Бег"),
        (TYPE_SKI, "Лыжи"),
        (TYPE_BIKE, "Велосипед"),
        (TYPE_SWIM, "Плавание"),
        (TYPE_HIKE_DAY, "Поход, полный день"),
        (TYPE_GSH_DAY, "Тренировочный выезд ГШ, полный день"),
    )

    DISTANCE_BASED_TYPES = {
        TYPE_RUN,
        TYPE_SKI,
        TYPE_BIKE,
        TYPE_SWIM,
    }
    FULL_DAY_TYPES = {
        TYPE_HIKE_DAY,
        TYPE_GSH_DAY,
    }

    challenge = models.ForeignKey(
        Challenge,
        on_delete=models.CASCADE,
        related_name="activities",
        verbose_name="Челлендж",
    )
    participant = models.ForeignKey(
        ChallengeParticipant,
        on_delete=models.CASCADE,
        related_name="activities",
        verbose_name="Участник",
    )
    source_message = models.ForeignKey(
        TelegramMessage,
        on_delete=models.SET_NULL,
        related_name="challenge_activities",
        blank=True,
        null=True,
        verbose_name="Сообщение Telegram",
        help_text="Исходное сообщение, из которого разобрана активность.",
    )
    source_order = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Порядок в сообщении",
        help_text="Нужен, когда в одном сообщении указано несколько тренировок.",
    )
    activity_type = models.CharField(
        max_length=32,
        choices=ACTIVITY_TYPE_CHOICES,
        verbose_name="Тип активности",
    )
    happened_on = models.DateField(verbose_name="Дата активности")
    distance_km = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name="Дистанция, км",
    )
    pace_minutes_per_km = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name="Темп, мин/км",
        help_text="Используется только для бега.",
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("happened_on", "id")
        verbose_name = "Активность челленджа"
        verbose_name_plural = "Активности челленджа"
        constraints = [
            models.UniqueConstraint(
                fields=("challenge", "participant", "happened_on"),
                name="challenge_activity_unique_participant_day",
            ),
            models.UniqueConstraint(
                fields=("source_message", "source_order"),
                condition=Q(source_message__isnull=False),
                name="challenge_activity_unique_message_order",
            ),
        ]
        indexes = [
            models.Index(
                fields=("challenge", "participant", "happened_on"),
                name="challenge_activity_score_idx",
            )
        ]

    def __str__(self):
        return f"{self.participant} {self.get_activity_type_display()} {self.happened_on}"

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.challenge_id
            and self.participant_id
            and self.participant.challenge_id != self.challenge_id
        ):
            errors["participant"] = "Участник должен относиться к тому же челленджу."

        if (
            self.challenge_id
            and self.happened_on
            and (
                self.happened_on < self.challenge.start_date
                or self.happened_on > self.challenge.end_date
            )
        ):
            errors["happened_on"] = "Дата активности должна попадать в период челленджа."

        if self.activity_type in self.DISTANCE_BASED_TYPES and self.distance_km is None:
            errors["distance_km"] = "Для этой активности нужна дистанция."

        if self.activity_type in self.FULL_DAY_TYPES and self.distance_km is not None:
            errors["distance_km"] = "Для активности полного дня дистанция не используется."

        if self.activity_type == self.TYPE_RUN and self.pace_minutes_per_km is None:
            errors["pace_minutes_per_km"] = "Для бега нужно указать темп."

        if self.activity_type != self.TYPE_RUN and self.pace_minutes_per_km is not None:
            errors["pace_minutes_per_km"] = "Темп хранится только для беговых тренировок."

        if self.source_message_id and self.source_order < 1:
            errors["source_order"] = "Порядковый номер должен быть больше нуля."

        if errors:
            raise ValidationError(errors)

    def _distance_or_zero(self):
        return self.distance_km or Decimal("0")

    @property
    def base_points(self):
        distance = self._distance_or_zero()

        if self.activity_type == self.TYPE_RUN:
            pace = self.pace_minutes_per_km
            if pace is None or pace >= Decimal("10"):
                return 0
            if distance > Decimal("10"):
                return 3
            if distance > Decimal("5"):
                return 2
            return 0

        if self.activity_type == self.TYPE_SKI:
            if distance >= Decimal("12"):
                return 3
            if distance >= Decimal("6"):
                return 2
            return 0

        if self.activity_type == self.TYPE_BIKE:
            if distance >= Decimal("40"):
                return 3
            if distance >= Decimal("20"):
                return 2
            return 0

        if self.activity_type == self.TYPE_SWIM:
            if distance >= Decimal("2"):
                return 3
            if distance >= Decimal("1"):
                return 2
            return 0

        if self.activity_type in self.FULL_DAY_TYPES:
            return 2

        return 0

    def _previous_scoring_activity(self):
        if not self.challenge_id or not self.participant_id or not self.happened_on:
            return None

        previous_activities = self.__class__.objects.filter(
            challenge_id=self.challenge_id,
            participant_id=self.participant_id,
            happened_on__lt=self.happened_on,
        ).order_by("-happened_on", "-id")

        if self.pk:
            previous_activities = previous_activities.exclude(pk=self.pk)

        for activity in previous_activities:
            if activity.base_points > 0:
                return activity

        return None

    @property
    def streak_bonus_points(self):
        if self.base_points == 0:
            return 0

        previous_activity = self._previous_scoring_activity()
        if previous_activity is None:
            return 0

        if (self.happened_on - previous_activity.happened_on).days <= 4:
            return 1

        return 0

    @property
    def total_points(self):
        return self.base_points + self.streak_bonus_points
