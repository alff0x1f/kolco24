"""Read-time GPS spike filter — a one-to-one port of the mobile apps' ``trackLines``.

Sources: ``ios-kolco24/kolco24/Core/Track/TrackLines.swift`` and
``android-kolco24/.../data/track/TrackModels.kt``. Keep the constants and rules in
step with them, so an exported track draws the same lines the participant sees in
the app.

The apps hold ``accuracy`` as a 32-bit float, this port as a double: results can
differ only at exact boundaries.

Two deliberate divergences:
- the hard cap is strict (``< 500``; the apps keep exactly 500). Android's network
  provider reports a cell-tower fix as accuracy ``500.0``, kilometres off, in groups
  too long and too sparse for the chain rules to drop;
- ``MAX_SPEED_MPS`` is 6 (21.6 km/h), not 14: races are on foot only. On a real race
  99% of good steps are under 4.5 m/s.
"""

import math
from typing import NamedTuple

HARD_CAP_ACCURACY_M = 500
MAX_SPEED_MPS = 6
SHORT_CHAIN_MAX_POINTS = 3
SHORT_CHAIN_MAX_DURATION_MS = 60_000
TAIL_ACCURACY_RATIO = 3
TAIL_REFERENCE_MIN_ACCURACY_M = 1
EARTH_RADIUS_M = 6_371_000.0


class TrackFix(NamedTuple):
    id: str
    install_id: str
    segment_id: str
    lat: float
    lon: float
    accuracy: float
    altitude: float | None
    time_ms: int
    boot_count: int | None
    elapsed_at: int


def point_time_ms(trusted_ms, gps_time_ms):
    """``trustedMs ?? wallMs`` of the apps; the server has no ``wall_ms``, and a zero
    ``trusted_ms`` counts as absent (as ``NullIf`` does for ``Team.start_time``)."""
    return trusted_ms or gps_time_ms


def sort_key(fix):
    """Reboot-safe order: ``elapsed_at`` resets on reboot, so it is not first."""
    boot = fix.boot_count if fix.boot_count is not None else -1
    return (fix.time_ms, boot, fix.elapsed_at, fix.id)


def haversine_m(lat1, lon1, lat2, lon2):
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    sin_lat = math.sin(d_lat / 2)
    sin_lon = math.sin(d_lon / 2)
    h = (
        sin_lat * sin_lat
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * sin_lon
        * sin_lon
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(max(h, 0.0), 1.0)))


def is_reachable(a, b):
    d = haversine_m(a.lat, a.lon, b.lat, b.lon)
    dt_ms = max(abs(b.time_ms - a.time_ms), 1000)
    excess = max(0.0, d - min(a.accuracy, b.accuracy))
    return excess / (dt_ms / 1000) <= MAX_SPEED_MPS


def is_short_chain(chain):
    return (
        len(chain) <= SHORT_CHAIN_MAX_POINTS
        and chain[-1].time_ms - chain[0].time_ms < SHORT_CHAIN_MAX_DURATION_MS
    )


def _median_accuracy(chain):
    values = sorted(p.accuracy for p in chain)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _split_when(items, cut):
    out = []
    for item in items:
        if out and not cut(out[-1][-1], item):
            out[-1].append(item)
        else:
            out.append([item])
    return out


def track_lines(points):
    """Split already-sorted (:func:`sort_key`) points into drawable lines.

    1. drop fixes with accuracy ``HARD_CAP_ACCURACY_M`` or worse;
    2. split into consecutive ``segment_id`` runs — lines never cross runs;
    3. cut each run into chains at every unreachable step;
    4. drop a short head before a long chain, a short interior chain whose bypass is
       reachable from the last accepted point, and a short tail right after a kept long
       chain with a ``TAIL_ACCURACY_RATIO``× better median accuracy;
    5. a kept chain joins the current line when reachable, else starts a new one.

    Lines are never empty.
    """
    kept = [p for p in points if p.accuracy < HARD_CAP_ACCURACY_M]
    runs = _split_when(kept, lambda a, b: a.segment_id != b.segment_id)
    return [line for run in runs for line in _run_lines(run)]


def _run_lines(run):
    chains = _split_when(run, lambda a, b: not is_reachable(a, b))
    if len(chains) == 1:
        return chains
    lines = []
    prev_kept_long = False
    for i, chain in enumerate(chains):
        short = is_short_chain(chain)
        if i == 0:
            drop = short and not is_short_chain(chains[1])
        elif not short:
            drop = False
        elif i < len(chains) - 1:
            drop = bool(lines) and is_reachable(lines[-1][-1], chains[i + 1][0])
        else:
            reference = max(
                _median_accuracy(chains[i - 1]), TAIL_REFERENCE_MIN_ACCURACY_M
            )
            drop = (
                prev_kept_long
                and _median_accuracy(chain) >= TAIL_ACCURACY_RATIO * reference
            )
        prev_kept_long = not drop and not short
        if drop:
            continue
        if lines and is_reachable(lines[-1][-1], chain[0]):
            lines[-1].extend(chain)
        else:
            lines.append(chain)
    return lines
