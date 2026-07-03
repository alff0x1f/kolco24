"""Build/freeze the denormalized results protocol for a race.

``build_protocol`` ports the on-the-fly scoring logic from the deprecated
``AllTeamsResultView`` into a snapshot: it writes ``ProtocolRow`` rows that
the results page reads verbatim, so a published protocol never drifts when
live ``Team``/``TakenKP`` data changes underneath it.
"""

from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.mobile.models import Mark, MarkPresent
from apps.race.models import Protocol, ProtocolRow
from website.models import Race
from website.models.checkpoint import Checkpoint
from website.models.models import Team


def _team_members(team):
    athlets = [
        team.athlet1,
        team.athlet2,
        team.athlet3,
        team.athlet4,
        team.athlet5,
        team.athlet6,
    ]
    # athlet1 always counts; athlet2..6 require ucount > their index (mirrors
    # the old view's "ucount > N and athletN+1" checks).
    members = [a for i, a in enumerate(athlets) if a and (i == 0 or team.ucount > i)]
    return ", ".join(members)


def _duration_str(duration_ms):
    if not duration_ms:
        return "-"
    seconds = int(duration_ms / 1000)
    minutes = int(seconds / 60)
    hours = int(minutes / 60)
    return f"{hours}:{minutes % 60:02d}:{seconds % 60:02d}"


def _score_checkpoints(team, cp_info):
    """Return NFC/photo checkpoint numbers + scores for ``team``.

    Reads the mobile ``Mark`` table (the Android app's checkpoint takes), not
    the legacy ``TakenKP``. ``cp_info`` maps ``checkpoint id -> (number, cost)``
    for the race's non-negative-cost КП — ``Mark.checkpoint_id`` is the
    checkpoint **id**, but the protocol displays/costs by **number**.

    Scoring rules (see the brainstorm + ``kolco24_app_v2`` UPLOAD.md):

    * A **КП counts as taken** only if the КП *and* all participants were
      scanned. For NFC that is ``method="nfc"`` **and** ``verified=True``
      (``cp_code`` proved a physical scan) **and** the number of distinct real
      (non-sentinel) present chips ``>= team.ucount`` — completeness is
      recomputed server-side against the **roster** (the team's own size), not
      trusting the client's ``expected_count``/``complete`` (per UPLOAD.md).
      The present count is a single ``Count`` annotation, so no per-mark query.
    * A **photo** take (``method="photo"``) counts on a known КП with no
      participant/verified check (photo is a fallback; members aren't scanned).
    * Takes are dedup'd by **``checkpoint_id``** (each physical КП scores once);
      NFC wins over photo for the same КП. Unknown ``checkpoint_id`` (not in
      ``cp_info``) is skipped.
    * ``chips_count`` = distinct non-sentinel ``nfc_uid`` across all the team's
      ``MarkPresent`` rows (how many bracelets participated).
    """
    real_present = Count(
        "present__nfc_uid",
        filter=Q(present__nfc_uid__isnull=False) & ~Q(present__nfc_uid=""),
        distinct=True,
    )
    nfc_marks = (
        Mark.objects.filter(team=team.id, method="nfc", verified=True)
        .annotate(present_real=real_present)
        .order_by(Coalesce("trusted_ms", "wall_ms"))
    )

    nfc_points = []
    nfc_score = 0
    seen_cps = set()
    for mark in nfc_marks:
        info = cp_info.get(mark.checkpoint_id)
        if (
            info
            and mark.checkpoint_id not in seen_cps
            and mark.present_real >= team.ucount
        ):
            number, cost = info
            nfc_score += cost
            nfc_points.append(number)
            seen_cps.add(mark.checkpoint_id)

    photo_marks = Mark.objects.filter(team=team.id, method="photo").order_by(
        "checkpoint_id"
    )
    photo_points = []
    photo_score = 0
    seen_photo = set()
    for mark in photo_marks:
        info = cp_info.get(mark.checkpoint_id)
        if (
            info
            and mark.checkpoint_id not in seen_cps
            and mark.checkpoint_id not in seen_photo
        ):
            number, cost = info
            photo_score += cost
            photo_points.append(number)
            seen_photo.add(mark.checkpoint_id)

    chips_count = (
        MarkPresent.objects.filter(mark__team=team.id, nfc_uid__isnull=False)
        .exclude(nfc_uid="")
        .values("nfc_uid")
        .distinct()
        .count()
    )

    return {
        "nfc_points": nfc_points,
        "nfc_score": nfc_score,
        "photo_points": photo_points,
        "photo_score": photo_score,
        "chips_count": chips_count,
    }


def _build_row(protocol, team, cp_info):
    scoring = _score_checkpoints(team, cp_info)
    total_score = scoring["nfc_score"] + scoring["photo_score"]

    duration_ms = 0
    if team.finish_time and team.start_time:
        duration_ms = team.finish_time - team.start_time
    duration_min = int(int(duration_ms / 1000) / 60)

    category = team.category2
    control = category.control_time if category else 0
    penalty = 0
    if control > 0 and duration_min > control:
        overtime_min = duration_min - control
        penalty = overtime_min * category.overtime_penalty

    final_score = total_score - penalty

    return ProtocolRow(
        protocol=protocol,
        team_id=team.id,
        start_number=team.start_number,
        team_name=team.teamname,
        members=_team_members(team),
        club=team.organization,
        city=team.city,
        member_count=team.ucount,
        category_id=team.category2_id,
        category_code=category.code if category else "",
        category_name=category.name if category else "",
        category_short_name=category.short_name if category else "",
        nfc_checkpoints=", ".join(str(p) for p in scoring["nfc_points"]),
        nfc_count=len(scoring["nfc_points"]),
        nfc_score=scoring["nfc_score"],
        photo_checkpoints=", ".join(str(p) for p in scoring["photo_points"]),
        photo_count=len(scoring["photo_points"]),
        photo_score=scoring["photo_score"],
        chips_count=scoring["chips_count"],
        total_score=total_score,
        start_time_ms=team.start_time,
        finish_time_ms=team.finish_time,
        duration_ms=duration_ms,
        duration_str=_duration_str(duration_ms),
        penalty=penalty,
        final_score=final_score,
        dnf=team.dnf,
    )


def _assign_places(rows):
    """Sort rows by category, then ``(-final_score, duration_ms, team_id)``;
    reset ``place`` to 1 at each category change.

    ``team_id`` is a tiebreaker so the order is deterministic across rebuilds
    even for two teams tied on both score and duration (the source queryset
    has no guaranteed row order).
    """
    rows.sort(key=lambda r: (r.category_id, -r.final_score, r.duration_ms, r.team_id))
    current_category = object()
    place = 1
    for row in rows:
        if row.category_id != current_category:
            current_category = row.category_id
            place = 1
        row.place = place
        place += 1
    return rows


def build_protocol(race, user):
    """Recompute a race's draft protocol from live data (or start a new one).

    Reuses the existing draft in place if the latest protocol is a draft
    (rows are wiped and rebuilt); otherwise creates a fresh draft. A
    ``select_for_update`` lock on the parent ``Race`` row (not the, possibly
    empty, ``Protocol`` queryset) serializes concurrent first-builds so two
    racing requests can't both create a draft.
    """
    with transaction.atomic():
        Race.objects.select_for_update().get(id=race.id)

        latest = Protocol.objects.filter(race=race).order_by("-created_at").first()
        reused_draft = latest is not None and latest.status == Protocol.DRAFT
        if reused_draft:
            protocol = latest
            protocol.rows.all().delete()
        else:
            protocol = Protocol.objects.create(
                race=race, status=Protocol.DRAFT, created_by=user
            )

        teams = (
            Team.objects.filter(category2__race_id=race.id, paid_people__gt=0)
            .exclude(start_time=0)
            .select_related("category2")
        )
        cp_info = {
            cp.id: (cp.number, cp.cost)
            for cp in Checkpoint.objects.filter(race_id=race.id, cost__gte=0)
        }

        rows = [_build_row(protocol, team, cp_info) for team in teams]
        _assign_places(rows)

        ProtocolRow.objects.bulk_create(rows)

        # Reusing a draft doesn't touch the Protocol row itself, so bump its
        # auto_now ``updated_at`` to reflect this rebuild (a fresh draft was
        # just inserted, so its ``updated_at`` is already current).
        if reused_draft:
            protocol.save(update_fields=["updated_at"])

    return protocol


def freeze_protocol(race):
    """Freeze the race's latest draft protocol into an immutable final.

    No-op (returns ``None``) when the latest protocol is already final or
    there is no protocol at all.
    """
    with transaction.atomic():
        Race.objects.select_for_update().get(id=race.id)

        protocol = Protocol.objects.filter(race=race).order_by("-created_at").first()
        if not protocol or protocol.status != Protocol.DRAFT:
            return None

        protocol.status = Protocol.FINAL
        protocol.frozen_at = timezone.now()
        protocol.save()
        return protocol
