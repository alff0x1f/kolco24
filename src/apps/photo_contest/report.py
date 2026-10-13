from collections import Counter, defaultdict

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.photo_contest.models import Comparison, PhotoEntry
from apps.photo_contest.pairing import PAIR_TTL
from apps.photo_contest.scoring import nomination_standings

AGREEMENT_MIN_ANSWERS = 10


def build_report(contest, nomination=None):
    """One row per voter, the least consensual first; see the plan for fields."""
    nominations = [nomination] if nomination else list(contest.nominations.all())
    scores = {}
    for item in nominations:
        for row in nomination_standings(item):
            scores[row["entry"].pk] = row["score"]

    comparisons = Comparison.objects.filter(nomination__in=nominations).values_list(
        "user_id", "left_id", "right_id", "winner_id", "is_void", "created_at", "ip"
    )
    stale_before = timezone.now() - PAIR_TTL
    stats = defaultdict(
        lambda: {
            "answered": 0,
            "void": 0,
            "abandoned": 0,
            "ips": set(),
            "agree": 0,
            "comparable": 0,
            "choices": Counter(),
        }
    )
    users_by_ip = defaultdict(set)
    for user_id, left_id, right_id, winner_id, is_void, created_at, ip in comparisons:
        s = stats[user_id]
        if ip:
            s["ips"].add(ip)
            users_by_ip[ip].add(user_id)
        if winner_id is None:
            if created_at < stale_before:
                s["abandoned"] += 1
            continue
        s["answered"] += 1
        s["void"] += is_void
        s["choices"][winner_id] += 1
        loser_id = right_id if winner_id == left_id else left_id
        # ``scores`` holds approved entries only, so this skips withdrawn photos.
        if winner_id in scores and loser_id in scores:
            if scores[winner_id] != scores[loser_id]:
                s["comparable"] += 1
                s["agree"] += scores[winner_id] > scores[loser_id]

    users = get_user_model().objects.in_bulk(stats.keys())
    top_ids = {
        s["choices"].most_common(1)[0][0] for s in stats.values() if s["choices"]
    }
    entries = PhotoEntry.objects.in_bulk(top_ids)

    rows = []
    for user_id, s in stats.items():
        top = s["choices"].most_common(1)
        shared = set().union(*(users_by_ip[ip] for ip in s["ips"])) - {user_id}
        rows.append(
            {
                "user": users[user_id],
                "answered": s["answered"],
                "abandoned": s["abandoned"],
                "ip_count": len(s["ips"]),
                "shared_ip_voters": len(shared),
                "agreement": (
                    round(100 * s["agree"] / s["comparable"])
                    if s["comparable"] >= AGREEMENT_MIN_ANSWERS
                    else None
                ),
                "top_entry": entries.get(top[0][0]) if top else None,
                "top_count": top[0][1] if top else 0,
                "is_void": s["answered"] > 0 and s["void"] == s["answered"],
            }
        )
    rows.sort(key=lambda r: (r["agreement"] is None, r["agreement"] or 0, r["user"].pk))
    return rows
