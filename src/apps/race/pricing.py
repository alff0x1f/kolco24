"""Shared team-charge + payment-creation helpers for race add-ons.

This module is the single source of truth for the team charge formula:

    fee      = max(0, int((ucount − paid_people) × race.current_price))
    discount = promo.discount_for(fee)          # 0 without a promo code
    total    = max(0, fee − discount
                 + Σ active extras: max(0, count − count_paid) × price)

A promo code (``apps/race/promo.py``) discounts the **participation fee only** —
add-ons are always charged at full price. The code's quota (how many teams may
still use it) is derived from ``Payment`` rows there, and re-checked here under a
row lock before the payment is created.

The client mirror of this formula lives in ``src/static/js/team-form.js``
(live total + per-extra steppers + the promo line). Keep the two in sync — any
change to the charge math here must be reflected there, and vice versa.
"""

from collections import namedtuple
from datetime import timedelta

from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone

from vtb.client import VTBClient
from website.models import Payment, Team, VTBPayment, VTBPreparedPayment

# One line of the add-on charge: which extra, how many units this payment
# covers (the delta), and the per-unit price snapshot at charge time.
ExtraCharge = namedtuple("ExtraCharge", ["race_extra", "count", "unit_price"])

# Longer than a checkout can run (VTB client timeouts 15 s + 20 s, gunicorn
# worker timeout 60 s). A draft still without a VTB order after this is
# stranded — its worker died mid-checkout — and must not block a new checkout.
CHECKOUT_IN_FLIGHT_TTL = timedelta(seconds=90)


class CheckoutInFlight(Exception):
    """Another request is still creating this team's VTB order."""


def checkout_in_flight(team):
    """Whether a request is still creating a VTB order for ``team``."""
    return Payment.objects.filter(
        team=team,
        status=Payment.STATUS_DRAFT,
        vtb_payment__isnull=True,
        created_at__gt=timezone.now() - CHECKOUT_IN_FLIGHT_TTL,
    ).exists()


def compute_team_charge(team, race, promo=None):
    """Return ``(total, lines, discount)`` for charging ``team`` on ``race``.

    ``total`` is the floored, non-negative integer amount to charge: the unpaid
    race-fee term, less the promo discount, plus — for each active ``RaceExtra``
    — the unpaid add-on delta at the extra's current price. ``lines`` is a list
    of ``ExtraCharge`` (one per extra with a nonzero delta) used to snapshot
    ``PaymentExtra`` rows. ``discount`` is the applied promo discount in ₽
    (0 without a code), snapshotted onto ``Payment.discount_amount``.

    The fee is rounded to an int **before** the discount: ``Team.paid_people`` is
    a ``FloatField``, and the JS mirror floors a percent the same way.
    """
    cost_now = race.current_price
    fee = max(0, int((int(team.ucount) - team.paid_people) * cost_now))
    discount = promo.discount_for(fee) if promo else 0
    total = fee - discount

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

    return max(0, int(total)), lines, discount


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


def create_team_payment(request, team, race, promo=None):
    """Create the ``Payment`` (+ ``PaymentExtra`` snapshots) and mint the VTB order.

    Returns the redirect ``HttpResponse`` to the VTB pay URL, or ``None`` when
    there is nothing to charge (caller redirects to its own success URL). Reads
    the team's ``TeamExtra`` rows — the caller must ``upsert_team_extras`` first.

    ``payment_method`` is forced to ``"sbp2"``: once extras are present,
    ``payment_amount`` intentionally diverges from ``paid_for × cost_per_person``,
    so the partial Yandex ``update_team`` back-calc must never run on these.

    With a ``promo``, the code's quota is re-checked under a row lock — the last
    free slot must not be handed to two teams checking out at once — and
    ``PromoUnavailable`` is raised if it went away since the form validated.

    Raises ``CheckoutInFlight`` while another request is still creating this
    team's order — a second order would charge the same seats twice.
    A promo that covers the whole fee charges nothing but still has to credit
    the seats, so the payment is created as a draft and settled in the same
    transaction.

    A team holds at most one payable order per promo: an open order for the same
    charge is reused (redirect to its pay URL), any other open order raises
    ``PromoUnavailable`` — see ``apps.race.promo.open_checkout``.
    """
    from apps.race.models import PaymentExtra, RacePromo
    from apps.race.promo import (
        PromoError,
        PromoUnavailable,
        check_available,
        open_checkout,
    )
    from apps.race.settlement import settle_payment

    # Nothing left to pay even before any discount → no payment at all. This
    # does not depend on the promo, so it is decided without taking the lock.
    gross, _, _ = compute_team_charge(team, race)
    if gross == 0:
        return None

    paid_for = int(team.ucount) - team.paid_people
    cost_now = race.current_price
    with transaction.atomic():
        # The team row lock serializes checkouts of one team, so two requests
        # cannot both see "nothing in flight" and mint two orders for the same
        # seats.
        Team.all_objects.select_for_update().only("pk").get(pk=team.pk)
        if checkout_in_flight(team):
            raise CheckoutInFlight
        if promo is not None:
            # The charge is computed from the locked row: the organizer may have
            # changed the code's value or type since the form validated.
            promo = RacePromo.objects.select_for_update().get(pk=promo.pk)
            if not promo.is_active:
                raise PromoUnavailable(str(PromoError("inactive")))
            try:
                check_available(promo, team)
            except PromoError as exc:
                raise PromoUnavailable(str(exc)) from exc
        cost, lines, discount = compute_team_charge(team, race, promo=promo)
        if promo is not None:
            existing = open_checkout(promo, team)
            if existing is not None:
                vtb = existing.vtb_payment
                if vtb is not None and vtb.status.upper() == "PAID":
                    # Paid at the bank, not settled by the poller yet.
                    raise PromoUnavailable(str(PromoError("already_used")))
                if vtb is not None and _same_charge(
                    existing, cost, paid_for, discount, lines
                ):
                    return _pay_redirect(vtb)
                raise PromoUnavailable(str(PromoError("checkout_open")))
        payment = Payment.objects.create(
            owner=request.user,
            team=team,
            payment_method="sbp2",
            payment_amount=cost,
            payment_with_discount=cost,
            cost_per_person=cost_now,
            paid_for=paid_for,
            promo=promo,
            discount_amount=discount,
            status=Payment.STATUS_DRAFT,
        )
        for line in lines:
            PaymentExtra.objects.create(
                payment=payment,
                race_extra=line.race_extra,
                count=line.count,
                unit_price=line.unit_price,
            )

        if cost == 0:
            # A promo ate the fee: still credit what was bought. Settled inside
            # this transaction because no VTBPayment exists for the poller to
            # recover a stranded draft, and while the promo row is still locked
            # so a parallel checkout for the same team sees the done payment.
            # settle_payment flips the draft to done — creating it as done would
            # trip its own idempotency guard and credit nothing.
            settle_payment(payment)
            return None

    vtb_client = VTBClient()
    vtb_client._ensure_token()

    try:
        payload = vtb_client.create_order(
            order_id=VTBPayment.new_order_id("ORDER"),
            order_name=f"Оплата за команду на Кольцо 24 ({payment.id})",
            amount_value=cost,
            return_payment_data="sbp",
        )
    except Exception:
        payment.delete()
        raise
    with transaction.atomic():
        vtb_payment = VTBPayment.from_vtb_payload(payload)
        payment.vtb_payment = vtb_payment
        payment.save(update_fields=["vtb_payment"])

    return _pay_redirect(vtb_payment)


def _same_charge(payment, cost, paid_for, discount, lines):
    """Whether ``payment`` covers exactly the charge about to be created."""
    stored_lines = {
        (pe.race_extra_id, pe.count, pe.unit_price) for pe in payment.extras.all()
    }
    new_lines = {(line.race_extra.id, line.count, line.unit_price) for line in lines}
    return (
        payment.payment_amount == cost
        and payment.paid_for == paid_for
        and payment.discount_amount == discount
        and stored_lines == new_lines
    )


def open_pay_redirect(team):
    """Redirect to the team's latest still-payable VTB order, or ``None``."""
    from apps.race.promo import open_draft_q

    payment = (
        Payment.objects.filter(team=team, vtb_payment__isnull=False)
        .filter(open_draft_q())
        .exclude(vtb_payment__status__iexact="PAID")
        .select_related("vtb_payment")
        .order_by("-created_at")
        .first()
    )
    if payment is None:
        return None
    return _pay_redirect(payment.vtb_payment)


def _pay_redirect(vtb_payment):
    prepared_payment = VTBPreparedPayment.objects.filter(payment=vtb_payment).first()
    if prepared_payment and prepared_payment.url:
        return HttpResponseRedirect(prepared_payment.url)
    return HttpResponseRedirect(vtb_payment.pay_url)
