from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from donate.models import DonateRequest
from vtb.client import VTBClient
from website.models import VTBPayment, VTBPreparedPayment


class DonateView(View):
    template_name = "donate/index.html"
    preset_amounts = (1500, 3000)
    preset_comments = ("ГШ 2 полугодие 2025", "ГШ 1 полугодие 2026")
    min_amount = Decimal("10")
    max_amount = Decimal("20000")
    comment_max_length = 255

    def get(self, request):
        return render(request, self.template_name, self.get_context())

    def post(self, request):
        amount = self.parse_amount(request.POST.get("amount", ""))
        sender_name = self.parse_sender_name(request.POST.get("sender_name", ""))
        comment = self.parse_comment(request.POST.get("comment", ""))
        if amount is None:
            messages.error(request, "Введите корректную сумму пожертвования")
            return render(
                request,
                self.template_name,
                self.get_context(
                    sender_name=request.POST.get("sender_name", ""),
                    comment=(request.POST.get("comment", "") or "").strip(),
                ),
            )

        if amount < self.min_amount:
            messages.error(
                request, f"Минимальная сумма взноса: {int(self.min_amount)} руб"
            )
            return render(
                request,
                self.template_name,
                self.get_context(
                    amount,
                    request.POST.get("sender_name", ""),
                    (request.POST.get("comment", "") or "").strip(),
                ),
            )

        if amount > self.max_amount:
            messages.error(
                request, f"Максимальная сумма взноса: {int(self.max_amount)} руб"
            )
            return render(
                request,
                self.template_name,
                self.get_context(
                    amount,
                    request.POST.get("sender_name", ""),
                    (request.POST.get("comment", "") or "").strip(),
                ),
            )

        if sender_name is None:
            messages.error(
                request,
                "Укажите, от кого взнос.",
            )
            return render(
                request,
                self.template_name,
                self.get_context(
                    amount,
                    request.POST.get("sender_name", ""),
                    (request.POST.get("comment", "") or "").strip(),
                ),
            )

        if comment is None:
            messages.error(
                request,
                "Введите комментарий.",
            )
            return render(
                request,
                self.template_name,
                self.get_context(
                    amount,
                    sender_name,
                    (request.POST.get("comment", "") or "").strip(),
                ),
            )

        donate_order_id = f"SPUTNIK_{uuid4().hex[:12].upper()}"
        try:
            payload = VTBClient().create_order(
                order_id=donate_order_id,
                order_name=f"Взнос ({donate_order_id})",
                amount_value=float(amount),
                return_payment_data="sbp",
            )
            vtb_payment = VTBPayment.from_vtb_payload(payload)
            DonateRequest.objects.update_or_create(
                payment=vtb_payment,
                defaults={
                    "sender_name": sender_name,
                    "comment": comment,
                },
            )
        except Exception:  # pragma: no cover - network/VTB errors
            messages.error(
                request,
                "Не удалось создать платеж. Попробуйте ещё раз чуть позже.",
            )
            return render(
                request,
                self.template_name,
                self.get_context(
                    amount,
                    sender_name,
                    (request.POST.get("comment", "") or "").strip(),
                ),
            )

        prepared_payment = VTBPreparedPayment.objects.filter(
            payment=vtb_payment
        ).first()
        if prepared_payment and prepared_payment.url:
            return redirect(prepared_payment.url)
        if vtb_payment.pay_url:
            return redirect(vtb_payment.pay_url)

        messages.error(
            request, "Платеж создан без ссылки на оплату. Обратитесь к организаторам."
        )
        return render(request, self.template_name, self.get_context(amount))

    def get_context(self, amount=None, sender_name="", comment=""):
        return {
            "preset_amounts": self.preset_amounts,
            "preset_comments": self.preset_comments,
            "amount": (
                str(amount) if amount is not None else str(self.preset_amounts[0])
            ),
            "min_amount": int(self.min_amount),
            "max_amount": int(self.max_amount),
            "sender_name": sender_name,
            "comment": comment,
        }

    @staticmethod
    def parse_amount(raw_amount):
        value = (raw_amount or "").strip().replace(",", ".")
        if not value:
            return None
        try:
            amount = Decimal(value)
        except InvalidOperation:
            return None
        if amount <= 0:
            return None
        return amount.quantize(Decimal("0.01"))

    @staticmethod
    def parse_sender_name(raw_name):
        value = " ".join((raw_name or "").split())
        if not value:
            return None
        return value

    def parse_comment(self, raw_comment):
        comment = " ".join((raw_comment or "").split())
        if not comment:
            return None
        if len(comment) > self.comment_max_length:
            return None
        return comment
