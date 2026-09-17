"""Crediting a confirmed race ``Payment`` to its team.

Moved here from ``website.management.commands.check_vtb_payments`` so the two
paths that settle a payment share one implementation: the VTB polling command
and the zero-charge path in ``apps/race/pricing.py`` (a promo code that covers
the whole fee — nothing to charge, but the seats still have to be credited).

Idempotency lives in ``Payment.status``: a payment already ``done`` credits
nothing on a second call. A caller that creates its own payment must therefore
create it as a ``draft`` and let :func:`settle_payment` flip it.

Возвраты идут в обратную сторону и ведут свой журнал: :func:`record_refund`
пишет строку ``PaymentRefund`` на каждый возврат банка, и её уникальный
``vtb_refund_id`` — единственный токен идемпотентности этой стороны.
"""

from typing import NamedTuple

from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Greatest
from django.utils import timezone

from website.models import Payment, PaymentRefund, Team


def credit_extras(team: Team, payment: Payment) -> None:
    """Credit add-on counts from this payment's ``PaymentExtra`` snapshots.

    Idempotency lives in the caller (the ``status == STATUS_DONE`` guard in
    :func:`settle_payment`), not in ``PaymentExtra`` — so this must only ever
    run once per payment.
    """
    from apps.race.models import TeamExtra

    for pe in payment.extras.all():
        te, _ = TeamExtra.objects.get_or_create(team=team, race_extra=pe.race_extra)
        # Atomic SQL-level increment avoids a read-modify-write race when
        # two command instances process different payments for the same team.
        TeamExtra.objects.filter(pk=te.pk).update(
            count_paid=F("count_paid") + pe.count,
            count=Greatest(F("count"), F("count_paid") + pe.count),
        )


def settle_payment(payment) -> bool:
    """Credit a confirmed race ``Payment`` exactly once.

    The flip to ``STATUS_DONE`` is claimed with a conditional ``UPDATE`` before
    anything is credited, so it is the single arbiter of who settles: a second
    call — a concurrent one, or one holding a stale in-memory copy — changes no
    row and credits nothing. Returns ``True`` only when the payment was settled
    on this call.
    """
    if not payment or payment.status == Payment.STATUS_DONE:
        return False
    team: Team = payment.team
    with transaction.atomic():
        claimed = (
            Payment.objects.filter(pk=payment.pk)
            .exclude(status=Payment.STATUS_DONE)
            .update(status=Payment.STATUS_DONE, order=payment.pk)
        )
        if not claimed:
            return False
        if team:
            # Atomic SQL-level increment, like credit_extras: two commands
            # settling two payments of the same team must not lose a credit.
            # ``all_objects``: a team may be deleted while its payment is still
            # a draft, and the bank can confirm that draft afterwards — the
            # default manager would silently skip the row and the money would
            # be lost, since the flip to ``done`` stops any later retry.
            Team.all_objects.filter(pk=team.pk).update(
                paid_people=F("paid_people") + payment.paid_for,
                paid_sum=F("paid_sum") + payment.payment_amount,
                updated_at=timezone.now(),
            )
            # Credit add-ons from the per-payment snapshots.
            credit_extras(team, payment)
            team.refresh_from_db(fields=["paid_people", "paid_sum"])
            from website.models.race import RegStatus

            category = team.category2
            race = category.race if category else None
            if (
                race
                and race.people_limit
                and race.reg_status == RegStatus.OPEN
                and race.people_count() >= race.people_limit
            ):
                race.reg_status = RegStatus.SOLD_OUT
                race.save(update_fields=["reg_status", "updated_at"])
    payment.status = Payment.STATUS_DONE
    payment.order = payment.pk
    return True


def debit_extras(team: Team, payment: Payment) -> None:
    """Take back the add-on counts this payment credited.

    The mirror of :func:`credit_extras`; ``count`` (what the team asked for) is
    left alone, only ``count_paid`` drops. Idempotency lives in the caller.
    """
    from apps.race.models import TeamExtra

    for pe in payment.extras.all():
        TeamExtra.objects.filter(team=team, race_extra=pe.race_extra).update(
            count_paid=Greatest(F("count_paid") - pe.count, 0),
        )


class RefundOutcome(NamedTuple):
    """Что сделал один вызов :func:`record_refund`."""

    recorded: bool  # строка возврата создана именно этим вызовом
    money: float  # деньги, снятые с команды (0, если возвращать уже нечего)
    people: float  # места, снятые с команды
    closing: bool  # этот возврат закрыл платёж целиком


def _refund_seats(payment, amount: float, closing: bool, people_before: float) -> float:
    """Места, которые списывает одна строка возврата.

    Закрывающая строка снимает весь остаток мест платежа: её сумма не обязана
    делиться на взнос — при промокоде платёж 700 ₽ при взносе 500 ₽ покрывает
    двоих, и правило кратности списало бы ноль.

    Для обычной частичной строки банк не говорит, что именно вернул, поэтому
    доказательством ухода людей считается только целое число взносов. Любая
    другая сумма (доп-услуга, сумма руками) двигает одни деньги, а места
    организатор правит сам.
    """
    if closing:
        return max(payment.paid_for - people_before, 0.0)
    cost = payment.cost_per_person
    if not cost or cost <= 0:
        return 0.0
    people = amount / cost
    rounded = round(people)
    if abs(people - rounded) > 0.001 or rounded <= 0:
        return 0.0
    return min(float(rounded), max(payment.paid_for - people_before, 0.0))


def record_refund(
    payment, refund_id: str, amount, refunded_at=None, status: str = ""
) -> RefundOutcome:
    """Записать один возврат банка и снять его деньги и места с команды.

    Идемпотентность — уникальный ``PaymentRefund.vtb_refund_id``, то есть ключ
    самого банка: повторный опрос заказа и любой порядок ``refunds[]`` в ответе
    ВТБ ничего не двигают. Известной строке обновляется только ``status``.

    Сколько уже возвращено, считается по самому журналу (``payment.refunds``) —
    копии этой суммы на платеже нет, чтобы ей было негде разойтись с ним.

    Инвариант: сумма возвратов по платежу не может превысить его
    ``payment_amount``, и снимать что-либо можно только с ``done``-платежа.
    Строка пишется в любом случае (след в аудите), но денег не двигает — иначе
    настоящий ``refundId``, пришедший по заказу, который до появления этой
    таблицы уже откатили, списал бы деньги второй раз, а возврат заказа,
    оплаченного и возвращённого между двумя опросами, снял бы с команды то,
    что ей никогда не начисляли.

    Возврат, закрывающий платёж целиком, переводит ``Payment`` ``done → cancel``
    и снимает доп-услуги: у частичного возврата разбивки по строкам нет, и
    гадать, какую услугу вернули, нечем.
    """
    if not payment or not refund_id:
        return RefundOutcome(False, 0.0, 0.0, False)
    amount = round(float(amount), 2)
    with transaction.atomic():
        locked = Payment.objects.select_for_update().filter(pk=payment.pk).first()
        if locked is None:
            return RefundOutcome(False, 0.0, 0.0, False)
        refund, created = PaymentRefund.objects.get_or_create(
            vtb_refund_id=refund_id,
            defaults={
                "payment": locked,
                "amount": 0.0,
                "people": 0.0,
                "refunded_at": refunded_at,
                "status": status,
            },
        )
        if not created:
            if status and refund.status != status:
                refund.status = status
                refund.save(update_fields=["status"])
            return RefundOutcome(
                False, 0.0, refund.people, locked.status == Payment.STATUS_CANCEL
            )

        # Что журнал уже забрал — единственный источник истины, денормализации
        # этих сумм на платеже нет: обе берутся одним агрегатом.
        before = locked.refunds.exclude(pk=refund.pk).aggregate(
            money=Sum("amount"), people=Sum("people")
        )
        money_before = before["money"] or 0.0
        people_before = before["people"] or 0.0
        # Снять деньги можно только с зачисленного платежа: ``draft`` команде
        # ничего не начислял, а ``cancel`` уже возвращён целиком.
        headroom = max(round(locked.payment_amount - money_before, 2), 0.0)
        money = min(amount, headroom) if locked.status == Payment.STATUS_DONE else 0.0
        closing = money > 0 and round(money_before + money, 2) >= round(
            locked.payment_amount, 2
        )
        people = _refund_seats(locked, money, closing, people_before) if money else 0.0

        refund.amount = money
        refund.people = people
        refund.save(update_fields=["amount", "people"])
        if not money:
            return RefundOutcome(True, 0.0, 0.0, locked.status == Payment.STATUS_CANCEL)

        if closing:
            Payment.objects.filter(pk=locked.pk).update(status=Payment.STATUS_CANCEL)
        team: Team = locked.team
        if team:
            Team.all_objects.filter(pk=team.pk).update(
                paid_people=Greatest(F("paid_people") - people, 0.0),
                paid_sum=Greatest(F("paid_sum") - money, 0.0),
                updated_at=timezone.now(),
            )
            if closing:
                debit_extras(team, locked)
            # Копия в памяти вызывающего не должна остаться с доснятыми числами.
            team.refresh_from_db(fields=["paid_people", "paid_sum"])
    if closing:
        payment.status = Payment.STATUS_CANCEL
    return RefundOutcome(True, money, people, closing)
