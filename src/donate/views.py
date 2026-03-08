from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from vtb.client import VTBClient
from website.models import VTBPayment, VTBPreparedPayment


class DonateView(View):
    template_name = "donate/index.html"
    preset_amounts = (1500, 3000)
    min_amount = Decimal("100")
    max_amount = Decimal("20000")

    def get(self, request):
        return render(request, self.template_name, self.get_context())

    def post(self, request):
        amount = self.parse_amount(request.POST.get("amount", ""))
        if amount is None:
            messages.error(request, "Введите корректную сумму пожертвования")
            return render(request, self.template_name, self.get_context())

        if amount < self.min_amount:
            messages.error(
                request, f"Минимальная сумма доната: {int(self.min_amount)} руб"
            )
            return render(request, self.template_name, self.get_context(amount))

        if amount > self.max_amount:
            messages.error(
                request, f"Максимальная сумма доната: {int(self.max_amount)} руб"
            )
            return render(request, self.template_name, self.get_context(amount))

        donate_order_id = f"SPUTNIK_{uuid4().hex[:12].upper()}"
        try:
            payload = VTBClient().create_order(
                order_id=donate_order_id,
                order_name=f"Взнос ({donate_order_id})",
                amount_value=float(amount),
                return_payment_data="sbp",
            )
            vtb_payment = VTBPayment.from_vtb_payload(payload)
        except Exception:  # pragma: no cover - network/VTB errors
            messages.error(
                request,
                "Не удалось создать платеж. Попробуйте ещё раз чуть позже.",
            )
            return render(request, self.template_name, self.get_context(amount))

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

    def get_context(self, amount=None):
        return {
            "preset_amounts": self.preset_amounts,
            "amount": str(self.preset_amounts[0]),
            "min_amount": int(self.min_amount),
            "max_amount": int(self.max_amount),
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
