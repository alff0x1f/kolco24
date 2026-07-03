"""Read-only aggregation for the admin «Данные приложения» pages.

Builds the context for :class:`apps.race.views.RaceAppDataView` (per-team
overview of everything the mobile app uploaded for a race) and
:class:`apps.race.views.RaceAppDataTeamView` (a single team's chronological
event feed). Pure reads over ``apps.mobile``'s ingest models (``Mark``/
``MarkPresent``/``MarkPhoto``/``TrackPoint``/``JudgeScan``) plus the member
``Tag`` pool — nothing here writes, so none of the ``update_fields``/signal
invariants apply.

Team attribution of judge scans: ``JudgeScan`` deliberately has no team FK, so
a scan is attributed to a team when its (normalized) ``nfc_uid`` appears among
the team's chips — the distinct ``MarkPresent.nfc_uid`` values of the team's
marks. Scans whose uid matches no team are surfaced separately (they must not
silently vanish — spotting them is half the point of the page).

``MarkPresent.nfc_uid``/``Mark.cp_nfc_uid`` are stored raw (see CLAUDE.md), so
every uid is normalized (``.strip().upper()``) here before matching against
the normalized ``Tag`` pool and ``JudgeScan.nfc_uid``.
"""

from collections import defaultdict
from datetime import datetime
from datetime import timezone as dt_timezone

from django.db.models import Count, Max, Min

from apps.mobile.models import JudgeScan, Mark, MarkPhoto, MarkPresent, TrackPoint
from website.models import Team
from website.models.checkpoint import Checkpoint
from website.models.enums import CheckpointType
from website.models.tag import Tag


def _norm_uid(value):
    """Normalize a raw nfc_uid for matching; ``None``/blank → ``None``."""
    value = (value or "").strip().upper()
    return value or None


def format_ms(timestamp_ms):
    """Epoch-ms → local ``dd.mm HH:MM:SS`` (same conversion as member_logs)."""
    if not timestamp_ms or timestamp_ms <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return str(timestamp_ms)
    return dt.astimezone().strftime("%d.%m %H:%M:%S")


def _format_dt(dt):
    """Aware datetime (server ``created_at``) → local ``dd.mm HH:MM:SS``."""
    if dt is None:
        return ""
    return dt.astimezone().strftime("%d.%m %H:%M:%S")


def _format_duration(duration_ms):
    if duration_ms is None or duration_ms < 0:
        return ""
    minutes, seconds = divmod(duration_ms // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}ч {minutes:02d}м"
    if minutes:
        return f"{minutes}м {seconds:02d}с"
    return f"{seconds}с"


def _event_ms(obj):
    """The take/scan moment: ``trusted_ms`` when positive, else ``wall_ms``."""
    if obj.trusted_ms and obj.trusted_ms > 0:
        return obj.trusted_ms
    return obj.wall_ms


# |trusted_ms − wall_ms| above this gets a «часы …» badge in the timeline.
CLOCK_SKEW_BADGE_MS = 60_000


def _clock_skew(obj):
    """Phone-clock divergence badge data for a ``Mark``/``JudgeScan``.

    ``_event_ms`` silently prefers ``trusted_ms``, hiding a skewed phone
    wall-clock — the key diagnostic in a disputed take time. When both sources
    are present and disagree by more than ``CLOCK_SKEW_BADGE_MS``, return
    ``{"label", "wall", "trusted"}`` for the badge; otherwise ``None``.
    """
    if not obj.trusted_ms or obj.trusted_ms <= 0 or not obj.wall_ms:
        return None
    skew = obj.wall_ms - obj.trusted_ms
    if abs(skew) < CLOCK_SKEW_BADGE_MS:
        return None
    direction = "спешат" if skew > 0 else "отстают"
    return {
        "label": f"часы {direction} на {_format_duration(abs(skew))}",
        "wall": format_ms(obj.wall_ms),
        "trusted": format_ms(obj.trusted_ms),
    }


def _tag_pool():
    """Global member-chip pool: normalized uid → bracelet number."""
    return {
        _norm_uid(uid): number
        for number, uid in Tag.objects.values_list("number", "nfc_uid")
        if _norm_uid(uid)
    }


def _chip_label(uid, tag_numbers):
    """Display entry for one chip uid; ``known`` = resolved in the Tag pool."""
    number = tag_numbers.get(uid)
    if number is not None:
        return {"label": f"№{number}", "uid": uid, "known": True}
    return {"label": uid, "uid": uid, "known": False}


def _present_label(present, tag_numbers):
    """Display entry for one ``MarkPresent`` row (handles the null sentinel)."""
    uid = _norm_uid(present.nfc_uid)
    if uid is None:
        # {nfc_uid: null, number: 0} is the "no snapshot" sentinel.
        return {"label": "без снимка", "uid": "", "known": False}
    return _chip_label(uid, tag_numbers)


def _race_teams(race):
    return list(
        Team.objects.filter(category2__race_id=race.id)
        .select_related("category2")
        .order_by("category2__order", "category2__id", "start_number", "id")
    )


def _boundary_cp_ids(race):
    """(start_cp_ids, finish_cp_ids) of the race's checkpoints."""
    start_ids, finish_ids = set(), set()
    rows = Checkpoint.objects.filter(race_id=race.id).values_list("id", "type")
    for cp_id, cp_type in rows:
        if cp_type == CheckpointType.start:
            start_ids.add(cp_id)
        elif cp_type == CheckpointType.finish:
            finish_ids.add(cp_id)
    return start_ids, finish_ids


def _chips_by_team(marks, presents_by_mark):
    """team_id → set of normalized chip uids seen in the team's marks."""
    mark_team = {m.id: m.team_id for m in marks}
    chips = defaultdict(set)
    for mark_id, rows in presents_by_mark.items():
        team_id = mark_team.get(mark_id)
        if team_id is None:
            continue
        for present in rows:
            uid = _norm_uid(present.nfc_uid)
            if uid:
                chips[team_id].add(uid)
    return chips


def _presents_by_mark(race):
    presents = defaultdict(list)
    qs = MarkPresent.objects.filter(mark__race_id=race.id).order_by(
        "mark_id", "number_in_team"
    )
    for present in qs:
        presents[present.mark_id].append(present)
    return presents


def build_overview(race):
    """Context for the per-team overview table plus unmatched judge scans."""
    teams = _race_teams(race)
    marks = list(Mark.objects.filter(race_id=race.id))
    presents_by_mark = _presents_by_mark(race)
    tag_numbers = _tag_pool()
    start_cp_ids, finish_cp_ids = _boundary_cp_ids(race)
    chips_by_team = _chips_by_team(marks, presents_by_mark)

    # Earliest verified NFC take per (team, boundary) — mirrors the aggregate
    # the /marks/ auto-populate uses (Min over positive trusted_ms|wall_ms).
    take_start, take_finish = {}, {}
    marks_total = defaultdict(int)
    marks_verified = defaultdict(int)
    for mark in marks:
        marks_total[mark.team_id] += 1
        if mark.verified:
            marks_verified[mark.team_id] += 1
        if not (mark.verified and mark.method == "nfc"):
            continue
        ms = _event_ms(mark)
        if not ms or ms <= 0:
            continue
        target = None
        if mark.checkpoint_id in start_cp_ids:
            target = take_start
        elif mark.checkpoint_id in finish_cp_ids:
            target = take_finish
        if target is not None:
            current = target.get(mark.team_id)
            if current is None or ms < current:
                target[mark.team_id] = ms

    # Judge scans: attribute by chip uid; a uid used by several teams (should
    # not happen, but data is data) shows up on every matching row.
    uid_to_teams = defaultdict(set)
    for team_id, uids in chips_by_team.items():
        for uid in uids:
            uid_to_teams[uid].add(team_id)

    judge = defaultdict(lambda: {"uids": set(), "first_ms": None, "last_ms": None})
    unmatched_scans = []
    for scan in JudgeScan.objects.filter(race_id=race.id):
        uid = _norm_uid(scan.nfc_uid)
        ms = _event_ms(scan)
        team_ids = uid_to_teams.get(uid, ())
        if not team_ids:
            unmatched_scans.append(
                {
                    "ms": ms,
                    "time": format_ms(ms),
                    "event_type": scan.event_type,
                    "participant_number": scan.participant_number,
                    "chip": _chip_label(uid, tag_numbers) if uid else None,
                    "received": _format_dt(scan.created_at),
                }
            )
            continue
        for team_id in team_ids:
            entry = judge[(team_id, scan.event_type)]
            entry["uids"].add(uid)
            if ms and ms > 0:
                if entry["first_ms"] is None or ms < entry["first_ms"]:
                    entry["first_ms"] = ms
                if entry["last_ms"] is None or ms > entry["last_ms"]:
                    entry["last_ms"] = ms
    unmatched_scans.sort(key=lambda scan: scan["ms"] or 0)

    track_stats = {
        row["team_id"]: row
        for row in TrackPoint.objects.filter(race_id=race.id)
        .values("team_id")
        .annotate(cnt=Count("id"), last_ms=Max("gps_time_ms"))
    }
    photo_counts = {
        row["mark__team_id"]: row["cnt"]
        for row in MarkPhoto.objects.filter(mark__race_id=race.id)
        .values("mark__team_id")
        .annotate(cnt=Count("id"))
    }

    def judge_cell(team_id, event_type, chip_count):
        entry = judge.get((team_id, event_type))
        if entry is None:
            return None
        return {
            "scanned": len(entry["uids"]),
            "chips": chip_count,
            "first": format_ms(entry["first_ms"]),
            "last": format_ms(entry["last_ms"]),
            "spread": entry["first_ms"] != entry["last_ms"],
        }

    rows = []
    for team in teams:
        chips = sorted(chips_by_team.get(team.id, ()), key=lambda u: (len(u), u))
        chip_labels = [_chip_label(uid, tag_numbers) for uid in chips]
        start_take_ms = take_start.get(team.id)
        finish_take_ms = take_finish.get(team.id)
        track = track_stats.get(team.id)
        rows.append(
            {
                "team": team,
                "chips": chip_labels,
                "start_time": format_ms(team.start_time),
                "finish_time": format_ms(team.finish_time),
                "take_start": format_ms(start_take_ms),
                "take_finish": format_ms(finish_take_ms),
                # A boundary take that disagrees with the stored Team time is
                # exactly the discrepancy this page exists to surface.
                "start_mismatch": bool(
                    team.start_time
                    and start_take_ms
                    and start_take_ms != team.start_time
                ),
                "finish_mismatch": bool(
                    team.finish_time
                    and finish_take_ms
                    and finish_take_ms != team.finish_time
                ),
                "judge_start": judge_cell(team.id, "start", len(chip_labels)),
                "judge_finish": judge_cell(team.id, "finish", len(chip_labels)),
                "marks_total": marks_total.get(team.id, 0),
                "marks_verified": marks_verified.get(team.id, 0),
                "photos": photo_counts.get(team.id, 0),
                "track_points": track["cnt"] if track else 0,
                "track_last": format_ms(track["last_ms"]) if track else "",
            }
        )
    return {"rows": rows, "unmatched_scans": unmatched_scans}


def build_team_timeline(race, team):
    """Context for a single team's page: header summary + chronological feed."""
    tag_numbers = _tag_pool()
    checkpoints = {cp.id: cp for cp in Checkpoint.objects.filter(race_id=race.id)}

    marks = list(Mark.objects.filter(race_id=race.id, team_id=team.id))
    mark_ids = [m.id for m in marks]
    presents = defaultdict(list)
    for present in MarkPresent.objects.filter(mark_id__in=mark_ids).order_by(
        "number_in_team"
    ):
        presents[present.mark_id].append(present)
    photos = defaultdict(list)
    for photo in MarkPhoto.objects.filter(mark_id__in=mark_ids).order_by("created_at"):
        photos[photo.mark_id].append(photo)

    chips = set()
    for rows in presents.values():
        for present in rows:
            uid = _norm_uid(present.nfc_uid)
            if uid:
                chips.add(uid)

    events = []
    installs = set()
    for mark in marks:
        installs.add(mark.source_install_id)
        cp = checkpoints.get(mark.checkpoint_id)
        ms = _event_ms(mark)
        events.append(
            {
                "kind": "mark",
                "ms": ms,
                "time": format_ms(ms),
                "received": _format_dt(mark.created_at),
                "cp_number": cp.number if cp else None,
                "cp_type": cp.type if cp else "",
                "cp_unknown": cp is None,
                "checkpoint_id": mark.checkpoint_id,
                "method": mark.method,
                "verified": mark.verified,
                "complete": mark.complete,
                "expected_count": mark.expected_count,
                "present": [_present_label(p, tag_numbers) for p in presents[mark.id]],
                "lat": mark.loc_lat,
                "lon": mark.loc_lon,
                "photos": [p.image.url for p in photos[mark.id] if p.image],
                "install_id": mark.source_install_id,
                "clock_skew": _clock_skew(mark),
            }
        )

    for scan in JudgeScan.objects.filter(race_id=race.id):
        uid = _norm_uid(scan.nfc_uid)
        if uid not in chips:
            continue
        ms = _event_ms(scan)
        events.append(
            {
                "kind": "judge",
                "ms": ms,
                "time": format_ms(ms),
                "received": _format_dt(scan.created_at),
                "event_type": scan.event_type,
                "participant_number": scan.participant_number,
                "chip": _chip_label(uid, tag_numbers),
                "clock_skew": _clock_skew(scan),
            }
        )

    segments = (
        TrackPoint.objects.filter(race_id=race.id, team_id=team.id)
        .values("install_id", "segment_id")
        .annotate(
            cnt=Count("id"), first_ms=Min("gps_time_ms"), last_ms=Max("gps_time_ms")
        )
    )
    for segment in segments:
        installs.add(segment["install_id"])
        events.append(
            {
                "kind": "track",
                "ms": segment["first_ms"],
                "time": format_ms(segment["first_ms"]),
                "last": format_ms(segment["last_ms"]),
                "duration": _format_duration(segment["last_ms"] - segment["first_ms"]),
                "points": segment["cnt"],
                "install_id": segment["install_id"],
                "segment_id": segment["segment_id"],
            }
        )

    boundaries = (
        ("Старт", "start_time", team.start_time),
        ("Финиш", "finish_time", team.finish_time),
    )
    for label, field, ms in boundaries:
        if ms:
            events.append(
                {
                    "kind": "boundary",
                    "ms": ms,
                    "time": format_ms(ms),
                    "label": label,
                    "field": field,
                }
            )

    events.sort(key=lambda event: event["ms"] or 0)
    return {
        "events": events,
        "start_time": format_ms(team.start_time),
        "finish_time": format_ms(team.finish_time),
        "chips": [
            _chip_label(uid, tag_numbers)
            for uid in sorted(chips, key=lambda u: (len(u), u))
        ],
        "installs": sorted(installs),
        "marks_count": len(marks),
        "photos_count": sum(len(rows) for rows in photos.values()),
    }
