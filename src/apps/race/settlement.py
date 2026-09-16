"""Crediting a confirmed race ``Payment`` to its team.

Moved here from ``website.management.commands.check_vtb_payments`` so the two
paths that settle a payment share one implementation: the VTB polling command
and the zero-charge path in ``apps/race/pricing.py`` (a promo code that covers
the whole fee — nothing to charge, but the seats still have to be credited).

Idempotency lives in ``Payment.status``: a payment already ``done`` credits
nothing on a second call. A caller that creates its own payment must therefore
create it as a ``draft`` and let :func:`settle_payment` flip it.
"""

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from website.models import Payment, Team


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

    The ``status == STATUS_DONE`` guard makes this idempotent: a second call for
    the same payment short-circuits and credits nothing. Returns ``True`` only
    when the payment was settled on this call.
    """
    if not payment or payment.status == Payment.STATUS_DONE:
        return False
    team: Team = payment.team
    with transaction.atomic():
        if team:
            team.paid_people += payment.paid_for
            team.paid_sum += payment.payment_amount
            team.save(
                update_fields=[
                    "paid_people",
                    "paid_sum",
                    "updated_at",
                ]
            )
            # Credit add-ons from the per-payment snapshots.
            credit_extras(team, payment)
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
        payment.save(update_fields=["status", "order"])
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


def refund_payment(payment) -> bool:
    """Undo a settled race ``Payment`` after the bank refunded its order.

    Only a ``done`` payment can be refunded, which also makes this idempotent:
    the flip to ``cancel`` means a second call takes nothing back twice. The
    race's ``reg_status`` is deliberately left as-is — a freed seat never
    reopens registration automatically (same rule as elsewhere).
    """
    if not payment or payment.status != Payment.STATUS_DONE:
        return False
    team: Team = payment.team
    with transaction.atomic():
        if team:
            Team.objects.filter(pk=team.pk).update(
                paid_people=Greatest(F("paid_people") - payment.paid_for, 0.0),
                paid_sum=Greatest(F("paid_sum") - payment.payment_amount, 0.0),
                updated_at=timezone.now(),
            )
            debit_extras(team, payment)
            # The caller's in-memory copy must not keep the pre-refund numbers.
            team.refresh_from_db(fields=["paid_people", "paid_sum"])
        payment.status = Payment.STATUS_CANCEL
        payment.save(update_fields=["status"])
    return True
