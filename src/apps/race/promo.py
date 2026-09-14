"""Promo-code resolution and quota accounting.

Usage is **derived** from ``Payment`` rows — ``RacePromo`` carries no counter and
there is no separate "use" table. One team may use a code once, so "how many
times was this code used" is exactly "how many *distinct teams* occupy it":

* a team with a ``done`` payment carrying the promo — a real use;
* a team with an *open* ``draft`` payment — one that may still settle (see
  :func:`open_draft_q`).

A draft is released only once the bank confirms its order can no longer be paid
(VTB status ``EXPIRED``), never by local age alone: ``check_vtb_payments`` settles
a ``PAID`` order unconditionally, however late it gets to it, so a quota slot
freed early could be redeemed twice. A cancelled payment frees it immediately.

``Payment.STATUS_DRAFT_WITH_INFO`` is deliberately **not** counted: it belongs to
the removed manual-verification flow that no live code path creates, and
``Race.reserved_people`` filters just as narrowly.
"""

from django.db.models import Q
from django.utils import timezone

from apps.race.models import RacePromo
from website.models.models import Payment
from website.models.race import RESERVATION_TTL

ERROR_MESSAGES = {
    "not_found": "Промокод не найден",
    "inactive": "Промокод больше не действует",
    "limit_reached": "Лимит промокода исчерпан",
    "already_used": "Ваша команда уже использовала этот промокод",
    "checkout_open": (
        "У команды уже есть неоплаченный заказ с этим промокодом на другую сумму. "
        "Оплатите его или дождитесь, пока он истечёт"
    ),
}


class PromoError(Exception):
    """A promo code the user entered cannot be applied. ``key`` says why."""

    def __init__(self, key):
        self.key = key
        super().__init__(ERROR_MESSAGES.get(key, "Промокод недоступен"))

    @property
    def message(self):
        return str(self)


class PromoUnavailable(Exception):
    """The quota went away between form validation and payment creation."""


def open_draft_q():
    """``Q`` for draft payments that may still settle.

    * With a VTB order: open until VTB reports ``EXPIRED``. ``expire_at`` is not
      enough — an order paid just before it passes stays ``CREATED`` locally
      until the poller catches up. ``PAID``-but-unsettled is open too.
    * Without one: a checkout in flight (the order is minted after the promo lock
      is released), open for ``RESERVATION_TTL``. After that it is stranded — the
      poller only sees ``VTBPayment`` rows, so nothing can settle it.
    """
    cutoff = timezone.now() - RESERVATION_TTL
    return Q(status=Payment.STATUS_DRAFT) & (
        Q(vtb_payment__isnull=True, created_at__gt=cutoff)
        | (Q(vtb_payment__isnull=False) & ~Q(vtb_payment__status__iexact="EXPIRED"))
    )


def occupied_team_ids(promo):
    """Return the ids of teams currently occupying ``promo``'s quota.

    ``exclude(team__isnull=True)`` is load-bearing: ``Payment.team`` is nullable,
    and a ``None`` in the set would make an unsaved ``Team()`` (the add flow)
    look like an already-occupying team and skip the ``max_uses`` check.
    """
    qs = (
        Payment.objects.filter(promo=promo)
        .exclude(team__isnull=True)
        .filter(Q(status=Payment.STATUS_DONE) | open_draft_q())
    )
    return set(qs.values_list("team_id", flat=True))


def open_checkout(promo, team):
    """Return ``team``'s unpaid ``promo`` payment that may still settle.

    Only ``done`` payments count as a use, so without this a team could mint a
    second discounted order while the first is still payable, and each would be
    settled on its own.
    """
    if team is None or team.pk is None:
        return None
    return (
        Payment.objects.filter(open_draft_q(), promo=promo, team_id=team.pk)
        .select_related("vtb_payment")
        .order_by("-created_at")
        .first()
    )


def check_available(promo, team=None):
    """Raise ``PromoError`` unless ``team`` may still apply ``promo``.

    ``team`` may be an unsaved ``Team()`` (the add flow): without a pk it cannot
    own a payment, so the per-team check is skipped and only the global limit
    applies. Django would otherwise compile ``filter(team=<unsaved>)`` into
    ``team_id IS NULL`` and silently answer the wrong question.
    """
    team_pk = getattr(team, "pk", None)
    if (
        team_pk is not None
        and Payment.objects.filter(
            promo=promo, team_id=team_pk, status=Payment.STATUS_DONE
        ).exists()
    ):
        raise PromoError("already_used")
    if promo.max_uses:
        occupied = occupied_team_ids(promo)
        if team_pk not in occupied and len(occupied) >= promo.max_uses:
            raise PromoError("limit_reached")
    return promo


def resolve_promo(race, code, team=None):
    """Resolve ``code`` within ``race`` for ``team``, or raise ``PromoError``."""
    code = (code or "").strip().upper()
    if not code:
        raise PromoError("not_found")
    promo = RacePromo.objects.filter(race=race, code=code).first()
    if promo is None:
        raise PromoError("not_found")
    if not promo.is_active:
        raise PromoError("inactive")
    return check_available(promo, team)
