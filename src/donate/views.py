from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from donate.models import ClubMember, DonateRequest, DonationPeriod, MemberDonation
from vtb.client import VTBClient
from website.models import VTBPayment, VTBPreparedPayment


class DonateView(View):
    template_name = "donate/index.html"
    preset_amounts = (1500, 3000)
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
        preset_comments = list(
            DonationPeriod.objects.filter(is_active=True)
            .order_by("-date")
            .values_list("name", flat=True)[:2]
        )
        return {
            "preset_amounts": self.preset_amounts,
            "preset_comments": preset_comments,
            "amount": (
                str(amount) if amount is not None else str(self.preset_amounts[0])
            ),
            "min_amount": int(self.min_amount),
            "max_amount": int(self.max_amount),
            "sender_name": sender_name,
            "comment": comment,
            "default_comment": preset_comments[0] if preset_comments else "",
            "donor_table": self.build_donor_table(),
        }

    @staticmethod
    def build_donor_table():
        # Периоды: от нового к старому (слева направо)
        periods = list(DonationPeriod.objects.filter(is_active=True).order_by("-date"))
        if not periods:
            return None

        period_ids = [p.id for p in periods]

        # Все записи по активным периодам
        donations = MemberDonation.objects.filter(
            period_id__in=period_ids
        ).select_related("member", "period")

        # member_id -> {period_id -> is_paid}
        payment_map = {}
        for d in donations:
            payment_map.setdefault(d.member_id, {})[d.period_id] = d.is_paid

        # Все участники у кого есть хоть одна запись
        members = list(
            ClubMember.objects.filter(id__in=payment_map.keys()).order_by("name")
        )

        latest_period_id = periods[0].id

        def row_sort_key(member):
            paid_latest = payment_map[member.id].get(latest_period_id, None)
            # Оплатил последний период → первые; остальные по имени
            return (0 if paid_latest else 1, member.name)

        members.sort(key=row_sort_key)

        # Строки таблицы: [{member, cells: [True/False/None, ...], paid_latest}]
        rows = []
        for member in members:
            pmap = payment_map[member.id]
            cells = [pmap.get(pid) for pid in period_ids]
            rows.append(
                {
                    "member": member,
                    "cells": cells,
                    "paid_latest": pmap.get(latest_period_id, None),
                }
            )

        return {
            "periods": periods,
            "rows": rows,
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
