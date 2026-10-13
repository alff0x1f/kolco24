import math
from collections import Counter

from django.db.models import Count

from apps.photo_contest.models import Comparison, PhotoEntry

Z = 1.96


def counted_comparisons(nomination):
    """Answered, non-void comparisons whose two photos are both approved now."""
    return Comparison.objects.filter(
        nomination=nomination,
        winner__isnull=False,
        is_void=False,
        left__status=PhotoEntry.APPROVED,
        right__status=PhotoEntry.APPROVED,
    )


def wilson_lower_bound(wins, n, z=Z):
    if wins == 0:
        # Exactly zero: the formula leaves float noise (±1e-17) that splits ties.
        return 0.0
    p = wins / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


def _counts(queryset, field):
    return Counter(dict(queryset.values_list(field).annotate(c=Count("pk")).order_by()))


def nomination_standings(nomination):
    """Approved entries ranked by Wilson score; equal scores share a place."""
    comparisons = counted_comparisons(nomination)
    wins = _counts(comparisons, "winner")
    shows = _counts(comparisons, "left") + _counts(comparisons, "right")
    entries = nomination.entries.filter(status=PhotoEntry.APPROVED).select_related(
        "uploaded_by"
    )
    rows = [
        {
            "entry": entry,
            "wins": wins[entry.pk],
            "n": shows[entry.pk],
            "score": wilson_lower_bound(wins[entry.pk], shows[entry.pk]),
        }
        for entry in entries
    ]
    rows.sort(key=lambda r: (-r["score"], -r["n"], r["entry"].pk))
    for index, row in enumerate(rows):
        if index and row["score"] == rows[index - 1]["score"]:
            row["place"] = rows[index - 1]["place"]
        else:
            row["place"] = index + 1
    return rows
