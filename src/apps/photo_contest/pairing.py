import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.photo_contest.models import Comparison, PhotoEntry
from apps.photo_contest.scoring import counted_comparisons

PAIR_TTL = timedelta(minutes=10)
# Never the default salt: magic links sign EmailVerification pks with it, so a
# pair token would double as a login link for the row with the same pk.
PAIR_TOKEN_SALT = "photo_contest.comparison"


def _answered(nomination, user):
    """All of the voter's answers, void included: voiding must not refill quota."""
    return Comparison.objects.filter(
        nomination=nomination, user=user, winner__isnull=False
    )


def answered_count(nomination, user):
    return _answered(nomination, user).count()


def lock_voter(user):
    """Serialize a voter's issue/answer; call inside ``transaction.atomic()``."""
    get_user_model().objects.select_for_update().get(pk=user.pk)


def select_pair(nomination, user):
    """Return ``(left, right)`` or ``None`` when no unanswered pair is left."""
    candidates = list(
        nomination.entries.filter(status=PhotoEntry.APPROVED).exclude(uploaded_by=user)
    )
    if len(candidates) < 2:
        return None
    shows = dict.fromkeys((e.pk for e in candidates), 0)
    for left_id, right_id in counted_comparisons(nomination).values_list(
        "left_id", "right_id"
    ):
        for pk in (left_id, right_id):
            if pk in shows:
                shows[pk] += 1
    answered = {
        frozenset(pair)
        for pair in _answered(nomination, user).values_list("left_id", "right_id")
    }
    random.shuffle(candidates)
    candidates.sort(key=lambda e: shows[e.pk])
    for first in candidates:
        opponents = [
            e
            for e in candidates
            if e.pk != first.pk and frozenset((first.pk, e.pk)) not in answered
        ]
        if opponents:
            pair = [first, random.choice(opponents)]
            random.shuffle(pair)
            return tuple(pair)
    return None


def outstanding_pair(nomination, user):
    """The voter's fresh unanswered pair with both photos approved, or ``None``.

    Stale rows are kept: the report counts them as abandoned.
    """
    row = (
        Comparison.objects.filter(
            nomination=nomination,
            user=user,
            winner__isnull=True,
            created_at__gte=timezone.now() - PAIR_TTL,
        )
        .select_related("left", "right")
        .order_by("-created_at", "-pk")
        .first()
    )
    if row and row.left.status == row.right.status == PhotoEntry.APPROVED:
        return row
    return None


def issue_pair(nomination, user):
    """Return the voter's current pair, creating one if needed; ``None`` if none."""
    with transaction.atomic():
        lock_voter(user)
        row = outstanding_pair(nomination, user)
        if row:
            return row
        pair = select_pair(nomination, user)
        if pair is None:
            return None
        return Comparison.objects.create(
            nomination=nomination, user=user, left=pair[0], right=pair[1]
        )
