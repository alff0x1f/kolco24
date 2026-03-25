from django.db import models


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
