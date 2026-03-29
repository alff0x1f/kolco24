from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


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
        return (
            self.training_labels.aggregate(points_sum=Sum("total_points"))["points_sum"] or 0
        )


class ChallengeTrainingLabel(models.Model):
    class Decision(models.TextChoices):
        COUNTED = "counted", "Засчитана"
        NOT_COUNTED = "not_counted", "Не засчитана"

    class TrainingType(models.TextChoices):
        RUN_5_10 = "run_5_10", "Бег 5-10 км"
        RUN_10_PLUS = "run_10_plus", "Бег 10+ км"
        SKI_6_12 = "ski_6_12", "Лыжи 6-12 км"
        SKI_12_PLUS = "ski_12_plus", "Лыжи 12+ км"
        BIKE_20_40 = "bike_20_40", "Велосипед 20-40 км"
        BIKE_40_PLUS = "bike_40_plus", "Велосипед 40+ км"
        SWIM_1_2 = "swim_1_2", "Плавание 1-2 км"
        SWIM_2_PLUS = "swim_2_plus", "Плавание 2+ км"
        HIKE_DAY = "hike_day", "Поход"
        GSH_DAY = "gsh_day", "Тренировочный выезд ГШ"

    TYPE_POINTS = {
        TrainingType.RUN_5_10: 2,
        TrainingType.RUN_10_PLUS: 3,
        TrainingType.SKI_6_12: 2,
        TrainingType.SKI_12_PLUS: 3,
        TrainingType.BIKE_20_40: 2,
        TrainingType.BIKE_40_PLUS: 3,
        TrainingType.SWIM_1_2: 2,
        TrainingType.SWIM_2_PLUS: 3,
        TrainingType.HIKE_DAY: 2,
        TrainingType.GSH_DAY: 2,
    }

    challenge = models.ForeignKey(
        Challenge,
        on_delete=models.CASCADE,
        related_name="training_labels",
        verbose_name="Челлендж",
    )
    participant = models.ForeignKey(
        ChallengeParticipant,
        on_delete=models.CASCADE,
        related_name="training_labels",
        verbose_name="Участник",
    )
    training_date = models.DateField(verbose_name="Дата тренировки")
    decision = models.CharField(
        max_length=32,
        choices=Decision.choices,
        verbose_name="Решение",
    )
    training_type = models.CharField(
        max_length=32,
        choices=TrainingType.choices,
        verbose_name="Тип тренировки",
    )
    base_points = models.PositiveSmallIntegerField(default=0, verbose_name="Базовые баллы")
    streak_bonus_points = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Бонус за серию",
    )
    total_points = models.PositiveSmallIntegerField(default=0, verbose_name="Итого баллов")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="challenge_training_labels",
        blank=True,
        null=True,
        verbose_name="Кто разметил",
    )
    reviewed_at = models.DateTimeField(default=timezone.now, verbose_name="Когда разметили")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    source_messages = models.ManyToManyField(
        TelegramMessage,
        through="ChallengeTrainingLabelMessage",
        related_name="training_labels",
        verbose_name="Сообщения-источники",
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("training_date", "id")
        verbose_name = "Размеченная тренировка"
        verbose_name_plural = "Размеченные тренировки"
        constraints = [
            models.UniqueConstraint(
                fields=("challenge", "participant", "training_date"),
                name="challenge_training_label_unique_day",
            )
        ]
        indexes = [
            models.Index(
                fields=("challenge", "participant", "training_date"),
                name="chall_lbl_score_idx",
            ),
            models.Index(
                fields=("challenge", "decision"),
                name="chall_lbl_dec_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.participant} {self.get_training_type_display()} {self.training_date}"
        )

    @classmethod
    def get_type_points(cls, training_type):
        return cls.TYPE_POINTS.get(training_type, 0)

    @classmethod
    def recalculate_scores_for_participant(cls, challenge_id, participant_id):
        previous_counted_date = None
        labels = cls.objects.filter(
            challenge_id=challenge_id,
            participant_id=participant_id,
        ).order_by("training_date", "id")

        for label in labels:
            base_points = (
                cls.get_type_points(label.training_type)
                if label.decision == cls.Decision.COUNTED
                else 0
            )
            streak_bonus = 0
            if (
                label.decision == cls.Decision.COUNTED
                and previous_counted_date is not None
                and (label.training_date - previous_counted_date).days <= 4
            ):
                streak_bonus = 1
            total_points = base_points + streak_bonus

            cls.objects.filter(pk=label.pk).update(
                base_points=base_points,
                streak_bonus_points=streak_bonus,
                total_points=total_points,
            )

            if label.decision == cls.Decision.COUNTED:
                previous_counted_date = label.training_date

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
            and self.training_date
            and (
                self.training_date < self.challenge.start_date
                or self.training_date > self.challenge.end_date
            )
        ):
            errors["training_date"] = "Дата тренировки должна попадать в период челленджа."

        if not self.training_type:
            errors["training_type"] = "Нужно указать тип тренировки."

        if not self.decision:
            errors["decision"] = "Нужно указать решение по тренировке."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        self.__class__.recalculate_scores_for_participant(
            self.challenge_id,
            self.participant_id,
        )
        self.refresh_from_db(fields=["base_points", "streak_bonus_points", "total_points"])

    def delete(self, *args, **kwargs):
        challenge_id = self.challenge_id
        participant_id = self.participant_id
        super().delete(*args, **kwargs)
        self.__class__.recalculate_scores_for_participant(challenge_id, participant_id)


class ChallengeTrainingLabelMessage(models.Model):
    label = models.ForeignKey(
        ChallengeTrainingLabel,
        on_delete=models.CASCADE,
        related_name="message_links",
        verbose_name="Разметка тренировки",
    )
    message = models.ForeignKey(
        TelegramMessage,
        on_delete=models.CASCADE,
        related_name="training_label_links",
        verbose_name="Сообщение Telegram",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Связь тренировки с сообщением"
        verbose_name_plural = "Связи тренировок с сообщениями"
        constraints = [
            models.UniqueConstraint(
                fields=("label", "message"),
                name="challenge_training_label_message_unique",
            )
        ]

    def __str__(self):
        return f"{self.label_id}:{self.message_id}"


class ChallengeMessageBatchReview(models.Model):
    class Resolution(models.TextChoices):
        LABELED = "labeled", "Есть тренировки"
        FLOOD = "flood", "Флуд"

    challenge = models.ForeignKey(
        Challenge,
        on_delete=models.CASCADE,
        related_name="message_batch_reviews",
        verbose_name="Челлендж",
    )
    participant = models.ForeignKey(
        ChallengeParticipant,
        on_delete=models.CASCADE,
        related_name="message_batch_reviews",
        verbose_name="Участник",
    )
    message_day = models.DateField(verbose_name="День сообщений")
    resolution = models.CharField(
        max_length=16,
        choices=Resolution.choices,
        verbose_name="Результат разбора",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="challenge_message_batch_reviews",
        blank=True,
        null=True,
        verbose_name="Кто разобрал",
    )
    reviewed_at = models.DateTimeField(default=timezone.now, verbose_name="Когда разобрали")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("message_day", "id")
        verbose_name = "Разбор пачки сообщений"
        verbose_name_plural = "Разборы пачек сообщений"
        constraints = [
            models.UniqueConstraint(
                fields=("challenge", "participant", "message_day"),
                name="challenge_message_batch_review_unique_day",
            )
        ]
        indexes = [
            models.Index(
                fields=("challenge", "participant", "message_day"),
                name="chall_batch_rev_idx",
            )
        ]

    def __str__(self):
        return f"{self.participant} {self.message_day} {self.get_resolution_display()}"

    def clean(self):
        super().clean()
        if (
            self.challenge_id
            and self.participant_id
            and self.participant.challenge_id != self.challenge_id
        ):
            raise ValidationError(
                {"participant": "Участник должен относиться к тому же челленджу."}
            )
