"""Finish states and an hourly forecast of arriving people.

Pure functions over plain values (milliseconds, minutes, people) — no ORM.
Every team on course is assumed to finish at a time on course
``D ~ N(КВ − MEAN_BEFORE_DEADLINE_MIN, SIGMA_MIN)``, known to lie between the
time already spent and its КВ; its people are spread over clock-hour buckets
by that probability.
"""

import datetime
import math

from django.utils import timezone

# A rough guess from past races: most teams arrive in the last hour before
# their КВ. Revisit after a race by comparing with the real finishes.
MEAN_BEFORE_DEADLINE_MIN = 40
SIGMA_MIN = 30
# An overdue team this late or less is "about to arrive"; later it most
# likely dropped out without a finish mark and is left out of the forecast.
OVERDUE_GRACE_MIN = 60

FINISHED = "finished"
NOT_STARTED = "not_started"
NO_CONTROL = "no_control"
OVERDUE = "overdue"
ON_COURSE = "on_course"

MINUTE_MS = 60_000
HOUR = datetime.timedelta(hours=1)
_MIN_WINDOW_MASS = 1e-9


def deadline_ms(start_ms, control_min):
    """The team's own КВ moment, or ``None`` without a start or a КВ."""
    if start_ms is None or control_min <= 0:
        return None
    return start_ms + control_min * MINUTE_MS


def team_state(start_ms, finish_ms, control_min, now_ms):
    """``(state, overdue_long)`` of one team.

    ``start_ms``/``finish_ms`` are ``None`` when unset or unusable.
    """
    if finish_ms is not None:
        return FINISHED, False
    if start_ms is None:
        return NOT_STARTED, False
    deadline = deadline_ms(start_ms, control_min)
    if deadline is None:
        return NO_CONTROL, False
    if now_ms >= deadline:
        return OVERDUE, now_ms - deadline > OVERDUE_GRACE_MIN * MINUTE_MS
    return ON_COURSE, False


def _local(ms):
    return timezone.localtime(
        datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    )


def _ms(moment):
    return int(moment.timestamp() * 1000)


def _floor_hour(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(moment):
    floor = _floor_hour(moment)
    return floor if floor == moment else floor + HOUR


def hour_buckets(now_ms, deadlines):
    """Clock-hour buckets ``[(from_ms, to_ms), …]`` from now past every КВ.

    The first bucket runs from now to the next full hour; the grid always
    holds at least that one, so overdue teams have somewhere to go. Buckets
    are ``(from, to]``: a КВ at 16:00 belongs to 15:00–16:00, and no empty
    hour is added after it.
    """
    if not deadlines:
        return []
    now = _local(now_ms)
    end = max(_floor_hour(now) + HOUR, _ceil_hour(_local(max(deadlines))))
    edges = [now]
    edge = _floor_hour(now) + HOUR
    while edge <= end:
        edges.append(edge)
        edge += HOUR
    return [(_ms(a), _ms(b)) for a, b in zip(edges, edges[1:])]


def _cdf(minutes, control_min):
    mean = control_min - MEAN_BEFORE_DEADLINE_MIN
    return 0.5 * (1 + math.erf((minutes - mean) / (SIGMA_MIN * math.sqrt(2))))


def spread_team(start_ms, control_min, people, now_ms, buckets):
    """People of one on-course team spread over ``buckets``.

    The time on course is conditioned on ``elapsed < D ≤ КВ``, so the
    spread always adds up to ``people``. Elapsed time is clamped at zero:
    a start "in the future" (a typo, a fast phone clock) must not put
    arrivals before the start.
    """
    shares = [0.0] * len(buckets)
    elapsed = max((now_ms - start_ms) / MINUTE_MS, 0.0)
    low = _cdf(elapsed, control_min)
    window = _cdf(control_min, control_min) - low
    if window < _MIN_WINDOW_MASS:
        deadline = deadline_ms(start_ms, control_min)
        target = next(
            (i for i, (lo, hi) in enumerate(buckets) if lo < deadline <= hi), 0
        )
        if buckets:
            shares[target] = float(people)
        return shares
    for i, (lo, hi) in enumerate(buckets):
        a = max((lo - start_ms) / MINUTE_MS, elapsed)
        b = min((hi - start_ms) / MINUTE_MS, control_min)
        if b > a:
            shares[i] = people * (_cdf(b, control_min) - _cdf(a, control_min)) / window
    return shares


def build_forecast(teams, category_ids, now_ms):
    """``{"all": rows, "<category id>": rows}`` on one shared bucket grid.

    ``teams`` are dicts with ``category_id``, ``people``, ``start_ms``,
    ``control_min``, ``state`` and ``overdue_long``. On-course teams are
    spread by the model; overdue ones within the grace go whole into the
    first bucket; the rest are left out. Every id in ``category_ids`` gets
    a key, even with nobody in the forecast.
    """
    counted = [
        t
        for t in teams
        if t["state"] == ON_COURSE or (t["state"] == OVERDUE and not t["overdue_long"])
    ]
    buckets = hour_buckets(
        now_ms, [deadline_ms(t["start_ms"], t["control_min"]) for t in counted]
    )
    totals = {key: [0.0] * len(buckets) for key in ["all", *map(str, category_ids)]}
    for team in counted:
        if team["state"] == OVERDUE:
            shares = [float(team["people"])] + [0.0] * (len(buckets) - 1)
        else:
            shares = spread_team(
                team["start_ms"], team["control_min"], team["people"], now_ms, buckets
            )
        for key in ("all", str(team["category_id"])):
            row = totals.setdefault(key, [0.0] * len(buckets))
            for i, share in enumerate(shares):
                row[i] += share
    labels = [
        (_local(lo).strftime("%H:%M"), _local(hi).strftime("%H:%M"))
        for lo, hi in buckets
    ]
    return {
        key: [
            {"from": start, "to": end, "people": round(people, 1)}
            for (start, end), people in zip(labels, row)
        ]
        for key, row in totals.items()
    }
