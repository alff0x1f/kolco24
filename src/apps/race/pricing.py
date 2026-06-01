"""Shared team-charge + payment-creation helpers for race add-ons.

This module is the single source of truth for the team charge formula:

    total = max(0, (ucount − paid_people) × race.current_price
                 + Σ active extras: max(0, count − count_paid) × price)

The client mirror of this formula lives in ``src/static/js/team-form.js``
(live total + per-extra steppers). Keep the two in sync — any change to the
charge math here must be reflected there, and vice versa.
"""

from collections import namedtuple

from django.db import transaction
from django.http import HttpResponseRedirect

from vtb.client import VTBClient
from website.models import Payment, VTBPayment, VTBPreparedPayment

# One line of the add-on charge: which extra, how many units this payment
# covers (the delta), and the per-unit price snapshot at charge time.
ExtraCharge = namedtuple("ExtraCharge", ["race_extra", "count", "unit_price"])


def compute_team_charge(team, race):
    """Return ``(total, lines)`` for charging ``team`` on ``race``.

    ``total`` is the floored, non-negative integer amount to charge:
    the unpaid race-fee term plus, for each active ``RaceExtra``, the unpaid
    add-on delta at the extra's current price. ``lines`` is a list of
    ``ExtraCharge`` (one per extra with a nonzero delta) used to snapshot
    ``PaymentExtra`` rows.
    """
    cost_now = race.current_price
    total = (int(team.ucount) - team.paid_people) * cost_now

    lines = []
    extras_by_id = {e.race_extra_id: e for e in team.extras.all()}
    for race_extra in race.extras.filter(is_active=True):
        te = extras_by_id.get(race_extra.id)
        count = te.count if te else 0
        count_paid = te.count_paid if te else 0
        delta = max(0, count - count_paid)
        if delta:
            total += delta * race_extra.price
            lines.append(
                ExtraCharge(
                    race_extra=race_extra, count=delta, unit_price=race_extra.price
                )
            )

    return max(0, int(total)), lines


def upsert_team_extras(team, cleaned_data, race):
    """Write ``TeamExtra.count`` from the form's ``extra_<code>`` fields.

    Get-or-creates one ``TeamExtra`` per active ``RaceExtra`` and sets its
    ``count`` from ``cleaned_data["extra_<code>"]`` (absent/None → 0).
    """
    from apps.race.models import TeamExtra

    for race_extra in race.extras.filter(is_active=True):
        count = cleaned_data.get(f"extra_{race_extra.code}") or 0
        te, _ = TeamExtra.objects.get_or_create(team=team, race_extra=race_extra)
        if te.count != count:
            te.count = count
            te.save(update_fields=["count"])


def create_team_payment(request, team, race):
    """Create the ``Payment`` (+ ``PaymentExtra`` snapshots) and mint the VTB order.

    Returns the redirect ``HttpResponse`` to the VTB pay URL, or ``None`` when
    the computed cost is 0 (caller redirects to its own success URL). Reads the
    team's ``TeamExtra`` rows — the caller must ``upsert_team_extras`` first.

    ``payment_method`` is forced to ``"sbp2"``: once extras are present,
    ``payment_amount`` intentionally diverges from ``paid_for × cost_per_person``,
    so the partial Yandex ``update_team`` back-calc must never run on these.
    """
    from apps.race.models import PaymentExtra

    cost, lines = compute_team_charge(team, race)
    if cost == 0:
        return None

    cost_now = race.current_price
    with transaction.atomic():
        payment = Payment.objects.create(
            owner=request.user,
            team=team,
            payment_method="sbp2",
            payment_amount=cost,
            payment_with_discount=cost,
            cost_per_person=cost_now,
            paid_for=int(team.ucount) - team.paid_people,
            status="draft",
        )
        for line in lines:
            PaymentExtra.objects.create(
                payment=payment,
                race_extra=line.race_extra,
                count=line.count,
                unit_price=line.unit_price,
            )

    vtb_client = VTBClient()
    vtb_client._ensure_token()

    payload = vtb_client.create_order(
        order_id=VTBPayment.new_order_id("ORDER"),
        order_name=f"Оплата за команду на Кольцо 24 ({payment.id})",
        amount_value=cost,
        return_payment_data="sbp",
    )
    with transaction.atomic():
        vtb_payment = VTBPayment.from_vtb_payload(payload)
        payment.vtb_payment = vtb_payment
        payment.save(update_fields=["vtb_payment"])

    prepared_payment = VTBPreparedPayment.objects.filter(payment=vtb_payment).first()
    if prepared_payment and prepared_payment.url:
        return HttpResponseRedirect(prepared_payment.url)
    return HttpResponseRedirect(vtb_payment.pay_url)
