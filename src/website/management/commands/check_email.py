from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Send a test email to verify that the email backend is configured correctly."

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Email address to send the test message to")

    def handle(self, *args, **options):
        recipient = options["recipient"]
        subject = "Тест отправки писем — Кольцо 24"
        message = (
            "Это тестовое письмо отправлено командой check_email.\n\n"
            f"Время: {timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        )
        from_email = "Кольцо 24 <org@kolco24.ru>"

        try:
            sent = send_mail(subject, message, from_email, [recipient], fail_silently=False)
        except Exception as exc:
            raise CommandError(f"Ошибка при отправке: {exc}") from exc

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Письмо поставлено в очередь → {recipient}"))
            self._show_queue_stats()
        else:
            self.stderr.write("send_mail вернул 0 — письмо не было отправлено.")

    def _show_queue_stats(self):
        try:
            from mailer.models import Message, MessageLog

            pending = Message.objects.count()
            sent_today = MessageLog.objects.filter(
                when_added__date=timezone.now().date(), result=MessageLog.RESULT_SUCCESS
            ).count()
            self.stdout.write(f"Очередь mailer: ожидает отправки — {pending}, отправлено сегодня — {sent_today}")
        except Exception:
            pass
