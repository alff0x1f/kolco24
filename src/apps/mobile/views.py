"""Mobile-app API views.

All views are gated by :class:`SignedAppPermission` (HMAC signature over a
canonical string). The base :class:`AppAPIView` records per-install usage stats
in :meth:`initial` — *after* permissions pass — and never lets a stats-write
failure break the response.
"""

import hashlib
import logging
import re

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Q, Sum
from django.http import HttpResponseNotModified
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from website.models.checkpoint import Checkpoint, CheckpointTag
from website.models.enums import CheckpointType
from website.models.models import Athlet, Team
from website.models.race import Category, Race

from .legend_crypto import build_bundle
from .models import (
    MARK_UPDATE_FIELDS,
    AppAuthFailure,
    AppInstall,
    Mark,
    MarkPhoto,
    MarkPresent,
    MobileToken,
    TrackPoint,
    _mark_photo_path,
)
from .permissions import CanEditRaceLegend, IsMobileUser, SignedAppPermission
from .serializers import (
    CategorySerializer,
    LegendCheckpointSerializer,
    LoginSerializer,
    MarkUploadSerializer,
    MemberTagSerializer,
    RaceListSerializer,
    TagCreateSerializer,
    TagSerializer,
    TeamSerializer,
    TrackUploadSerializer,
)
from .throttling import ClientIPScopedRateThrottle
from .tokens import generate_token
from .versioning import (
    active_member_tags,
    legend_version,
    member_tags_version,
    races_version,
    teams_version,
)

logger = logging.getLogger(__name__)


class AppAPIView(APIView):
    """Base view for signed mobile-app endpoints: verify signature, record stats."""

    authentication_classes = []
    permission_classes = [SignedAppPermission]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # runs check_permissions
        self._record_install(request)

    def permission_denied(self, request, message=None, code=None):
        """Log + record the denial, then raise the neutral 403.

        DRF calls this from ``check_permissions`` (inside ``initial``) when a
        permission returns ``False`` — *before* ``_record_install`` runs, so a
        denied request produces an ``AppAuthFailure`` row but never an
        ``AppInstall`` row. The reason flows only to the log/DB; the
        ``super()`` call keeps the client-facing 403 the neutral ``"Forbidden"``.
        """
        self._record_denial(request)
        super().permission_denied(request, message=message, code=code)

    def _record_denial(self, request):
        """Best-effort 403 log + aggregated DB row — must never break the 403.

        Only fires for HMAC-layer failures (``app_denial`` set by
        ``SignedAppPermission``).  Authorization denials from later layers (e.g.
        ``CanEditRaceLegend`` returning ``False``) do not set ``app_denial`` and
        are silently skipped so they don't pollute the AppAuthFailure table.
        """
        d = getattr(request, "app_denial", None)
        if not d:
            return
        reason = d.get("reason", "unknown")
        ip = d.get("ip") or "0.0.0.0"
        key_id = d.get("key_id", "")
        path = d.get("path", "")
        install = d.get("install", "")
        logger.warning(
            "Mobile app 403: reason=%s ip=%s key_id=%s path=%s install=%s",
            reason,
            ip,
            key_id,
            path,
            install,
        )
        try:
            AppAuthFailure.objects.update_or_create(
                ip=ip,
                key_id=key_id,
                reason=reason,
                defaults={"last_path": path, "last_install_id": install},
            )
            AppAuthFailure.objects.filter(ip=ip, key_id=key_id, reason=reason).update(
                count=F("count") + 1,
                last_seen=timezone.now(),
            )
        except Exception:
            logger.exception("Failed to record AppAuthFailure")

    def _record_install(self, request):
        """Best-effort per-install stat write — must never break the response."""
        meta = getattr(request, "app_meta", None)
        if not meta:
            return
        try:
            install_id = meta["install_id"]
            AppInstall.objects.update_or_create(
                install_id=install_id,
                defaults={
                    "platform": meta.get("platform", ""),
                    "app_version": meta.get("app_version", ""),
                    # AppInstall.key_id is max_length=32; truncate here, not in
                    # app_meta (where the full key_id is needed for MOBILE_APP_KEYS
                    # lookup and legend fingerprinting).
                    "key_id": meta.get("key_id", "")[:32],
                    "last_ip": meta.get("ip"),
                },
            )
            AppInstall.objects.filter(install_id=install_id).update(
                request_count=F("request_count") + 1,
                last_seen=timezone.now(),
            )
        except Exception:
            logger.exception("Failed to record AppInstall stats")


class LoginView(AppAPIView):
    """``POST /app/login/`` — exchange email+password for an opaque bearer token.

    Gated by :class:`SignedAppPermission` **only** (the per-build HMAC), so even
    this password endpoint is reachable only from one of our builds; it mints no
    token of its own and requires no bearer. On success it authenticates via the
    project's :class:`apps.accounts.backends.EmailBackend` (``email__iexact``),
    creates a :class:`MobileToken` row (storing only the sha256 hash) and returns
    the raw token **once** plus ``expires_at``.

    Failures are deliberately enumeration-safe: a wrong password and an unknown
    email both return ``401`` with the **same** generic message, never hinting
    which was wrong. A malformed body (missing field / non-JSON) is a ``400``
    from the serializer — distinct from the 401, but it leaks nothing about
    account existence.

    Throttled by an IP-scoped :class:`ClientIPScopedRateThrottle`
    (``mobile-login``) — keyed by the un-spoofable client IP (``X-Real-IP``
    set by nginx, or last ``X-Forwarded-For`` entry as fallback), with no
    ``request.data`` read inside the throttle, so it cannot re-trip the
    body-read ordering hazard.
    """

    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = "mobile-login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {"detail": "Неверный email или пароль"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        raw, token_hash = generate_token()
        expires_at = timezone.now() + settings.MOBILE_TOKEN_TTL
        MobileToken.objects.create(
            user=user, token_hash=token_hash, expires_at=expires_at
        )
        return Response({"token": raw, "expires_at": expires_at})


class LogoutView(AppAPIView):
    """``POST /app/logout/`` — revoke the presented bearer token.

    Stacks :class:`SignedAppPermission` (build HMAC) then :class:`IsMobileUser`
    (identity), so it is reachable only from one of our builds **and** with a
    valid token. It flips ``revoked_at`` on the exact token presented in the
    ``Authorization`` header — other tokens of the same user stay valid (each
    login mints its own row; revocation is per-device, not per-account).
    """

    permission_classes = [SignedAppPermission, IsMobileUser]

    def post(self, request):
        token = request.mobile_token
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        return Response(status=status.HTTP_200_OK)


class TagCreateView(AppAPIView):
    """``POST /app/race/<race_id>/tags/`` — bind a scanned NFC chip to a КП.

    The online-only provisioning endpoint: it creates a :class:`CheckpointTag`
    via ``instance.save()`` so the existing legend-crypto ``post_save`` signal
    fires (``ensure_code`` / ``build_bundle``), keeping the server the single
    source of crypto. The response carries the freshly-minted hex ``code`` for
    the app to write into the chip's NFC user memory, plus ``bid`` /
    ``checkpoint_id`` (``Checkpoint.id``) / ``number`` (``Checkpoint.number``) /
    ``nfc_uid``.

    Permission stack (order matters):

    1. :class:`SignedAppPermission` — per-build HMAC (over the request **body**);
    2. :class:`IsMobileUser` — resolves the bearer to ``request.mobile_user``;
    3. :class:`CanEditRaceLegend` — per-race ``can_edit_race`` authorization.

    Idempotency / conflicts (see the plan's §Tag-create):

    - same ``nfc_uid`` already on the **same** КП → idempotent 200 (no duplicate);
    - same ``nfc_uid`` on a **different** КП → 409 (never auto-rebind);
    - КП id not in this race, or a ``type="hidden"`` КП → 404.
    """

    permission_classes = [SignedAppPermission, IsMobileUser, CanEditRaceLegend]
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = "mobile-write"

    @staticmethod
    def _tag_response(tag, http_status):
        """Build the ``{bid, checkpoint_id, number, nfc_uid, code}`` payload.

        A tag created bypassing the signals (``bid == ""`` / ``code is None``)
        is repaired via :func:`build_bundle` before responding, so the hex of a
        missing ``code`` is never attempted (``None.hex()`` → 500). The repair
        runs under ``select_for_update`` and re-checks the row inside the lock so
        two concurrent repairs can't mint different codes — the first response
        could otherwise be written to the chip before the second overwrites the
        code in the DB.
        """
        if not tag.bid or tag.code is None:
            with transaction.atomic():
                tag = CheckpointTag.objects.select_for_update().get(pk=tag.pk)
                if not tag.bid or tag.code is None:
                    build_bundle(tag)
                    tag.refresh_from_db()
        code = bytes(tag.code) if tag.code is not None else None
        return Response(
            {
                "bid": tag.bid,
                "checkpoint_id": tag.checkpoint_id,
                "number": tag.checkpoint.number,
                "nfc_uid": tag.nfc_uid,
                "code": code.hex() if code is not None else None,
            },
            status=http_status,
        )

    def post(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)

        serializer = TagCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checkpoint_id = serializer.validated_data["checkpoint_id"]
        nfc_uid = serializer.validated_data["nfc_uid"].strip().upper()

        cp = get_object_or_404(
            Checkpoint.objects.exclude(type=CheckpointType.hidden.value),
            pk=checkpoint_id,
            race_id=race_id,
        )

        existing = list(CheckpointTag.objects.filter(nfc_uid=nfc_uid))
        for tag in existing:
            if tag.checkpoint_id == cp.id:
                # Same chip, same КП → idempotent success (no duplicate row).
                return self._tag_response(tag, status.HTTP_200_OK)
        if existing:
            # Chip already bound to a different КП — refuse to auto-rebind.
            return Response(
                {"detail": "Этот тег уже привязан к другому КП"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                tag = CheckpointTag(
                    checkpoint=cp, nfc_uid=nfc_uid, created_by=request.mobile_user
                )
                tag.save()  # fires post_save → ensure_code/build_bundle
        except IntegrityError as original_exc:
            # Could be the nfc_uid unique constraint (expected) or an unrelated
            # failure (FK violation, crypto signal, etc.). Re-query to determine.
            # Use filter().first() to avoid a nested exception context that would
            # re-raise CheckpointTag.DoesNotExist instead of the original error.
            tag = CheckpointTag.objects.filter(checkpoint=cp, nfc_uid=nfc_uid).first()
            if tag is not None:
                return self._tag_response(tag, status.HTTP_200_OK)
            # No row for this (checkpoint, nfc_uid). Only return 409 if the UID
            # was claimed by a different КП (concurrent race); otherwise
            # re-raise so an unrelated DB failure surfaces as a 500.
            if CheckpointTag.objects.filter(nfc_uid=nfc_uid).exists():
                return Response(
                    {"detail": "Этот тег уже привязан к другому КП"},
                    status=status.HTTP_409_CONFLICT,
                )
            raise original_exc

        tag.refresh_from_db()
        return self._tag_response(tag, status.HTTP_201_CREATED)


class TrackUploadView(AppAPIView):
    """``POST /app/race/<race_id>/track/`` — ingest a batch of GPS track points.

    The **third POST** under ``/app/`` but, unlike ``login`` and tag-create, it
    is **build-HMAC-only** (the default ``[SignedAppPermission]``) — it is **not**
    part of the per-person write layer. Track recording runs on participants'
    phones (not admins), so there is no bearer token; the trust boundary is the
    same as the read endpoints — a genuine build may post a track for any
    ``team_id`` in the race, and ``team_id``/``install_id`` are accepted as-is.

    Semantics:

    - ``install_id`` is taken from ``request.app_meta`` (the ``X-Install-Id``
      header, set by :class:`SignedAppPermission` before the view runs) so a
      team recording from two phones is separable — zero app change.
    - ``accepted`` echoes **all** submitted ids. The PK is the client UUID, so
      :meth:`bulk_create` with ``ignore_conflicts=True`` either inserts a new
      row or silently skips a re-sent one — both are success for the client's
      retry loop, which only wants confirmation it can stop resending.
    - Validation is **all-or-nothing**: a malformed/out-of-range point (or an
      over-500 batch) fails the whole request with a 400, never a partial accept.

    Rows are immutable and write-only — ``TrackPoint`` has no ``updated_at`` and
    stays out of ``versioning.py``/ETag/``sync`` machinery.
    """

    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = "mobile-write"

    def post(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)

        serializer = TrackUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # 400 on malformed/out-of-range
        team_id = serializer.validated_data["team_id"]
        points = serializer.validated_data["points"]

        if not Team.objects.filter(pk=team_id, category2__race_id=race_id).exists():
            return Response(
                {"detail": "Команда не найдена в этой гонке"},
                status=status.HTTP_404_NOT_FOUND,
            )

        install_id = request.app_meta["install_id"]
        objs = [
            TrackPoint(
                id=p["id"],
                team_id=team_id,
                race_id=race_id,
                install_id=install_id,
                segment_id=p["segment_id"],
                lat=p["lat"],
                lon=p["lon"],
                accuracy=p["accuracy"],
                altitude=p.get("altitude"),
                vertical_accuracy=p.get("vertical_accuracy"),
                gps_time_ms=p["gps_time_ms"],
                trusted_ms=p.get("trusted_ms"),
                elapsed_at=p["elapsed_at"],
                boot_count=p.get("boot_count"),
            )
            for p in points
        ]
        TrackPoint.objects.bulk_create(objs, ignore_conflicts=True)
        return Response(
            {"accepted": [p["id"] for p in points]}, status=status.HTTP_200_OK
        )


def _bids_by_checkpoint(race_id):
    """Map ``checkpoint_id -> {bid, ...}`` for a race's built tags.

    A single query (no per-mark lookup). ``.exclude(bid="")`` drops un-built
    rows (``bid`` default ``""``, created bypassing the ``build_bundle`` signal),
    mirroring the legend view. A КП may carry several tags, hence a set per КП.
    """
    bids_by_cp = {}
    for cp_id, bid in (
        CheckpointTag.objects.filter(checkpoint__race_id=race_id)
        .exclude(bid="")
        .values_list("checkpoint_id", "bid")
    ):
        bids_by_cp.setdefault(cp_id, set()).add(bid)
    return bids_by_cp


def _is_verified(bids_by_cp, checkpoint_id, cp_code_hex):
    """``True`` iff ``sha256(bytes.fromhex(cp_code))[:16]`` matches a tag bid.

    Proof the КП was physically scanned. A non-hex ``cp_code`` (e.g. a blank for
    a future ``photo`` mark), an unknown ``checkpoint_id``, or a mismatch all
    yield ``False`` — the row is still stored.
    """
    if not cp_code_hex:
        return False
    try:
        digest = hashlib.sha256(bytes.fromhex(cp_code_hex)).hexdigest()[:16]
    except ValueError:
        return False  # non-hex cp_code
    return digest in bids_by_cp.get(checkpoint_id, set())


class MarkUploadView(AppAPIView):
    """``POST /app/race/<race_id>/marks/`` — ingest a batch of checkpoint takes.

    A near-clone of :class:`TrackUploadView`: same ``AppAPIView`` base, same
    **build-HMAC-only** trust boundary (no per-person bearer — takes run on
    participants' phones), same ``mobile-write`` throttle, same client-UUID-PK
    idempotency. ``team_id``/``source_install_id`` are spoofable and accepted
    as-is; ``source_install_id`` is read from the **signed body** (the contract's
    provenance key), not the ``X-Install-Id`` header.

    **One deliberate divergence from ``/track/``**: a ``Mark`` is **not**
    immutable. The client deliberately re-sends the same ``id`` when a late GPS
    fix (``location``) or a new roster member (``present``) lands after the DTO
    was serialized but before the row was marked uploaded. So a repeat ``id``
    must **enrichment-merge**, not no-op: ``Mark`` upserts via
    ``bulk_create(update_conflicts=True, unique_fields=["id"],
    update_fields=MARK_UPDATE_FIELDS)`` (Postgres ``ON CONFLICT (id) DO UPDATE``,
    last-write-wins on the scalars, ``created_at`` preserved) and ``MarkPresent``
    inserts additively via ``bulk_create(ignore_conflicts=True)`` (the
    ``unique_together("mark", "number_in_team")`` skips already-stored slots).

    ``update_conflicts`` 500s (``CardinalityViolation``) on an in-batch duplicate
    conflict key, so the view **de-dups ``marks`` by ``id`` (keeping the last
    occurrence)** before building the upsert objects; ``accepted`` still echoes
    every originally-submitted id.

    ``verified`` is computed at ingest from a single per-batch prefetch of the
    race's tag bids (see :func:`_is_verified`); an unknown КП / mismatch / bad
    hex stores the row with ``verified=False``. ``Mark`` carries no ``updated_at``
    and stays out of ``versioning.py`` (nothing reads a version off it).
    """

    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = "mobile-write"

    def post(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)

        serializer = MarkUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # 400 on malformed/out-of-range
        team_id = serializer.validated_data["team_id"]
        source_install_id = serializer.validated_data["source_install_id"]
        marks = serializer.validated_data["marks"]

        if not Team.objects.filter(pk=team_id, category2__race_id=race_id).exists():
            return Response(
                {"detail": "Команда не найдена в этой гонке"},
                status=status.HTTP_404_NOT_FOUND,
            )

        accepted = [m["id"] for m in marks]

        if not marks:
            return Response({"accepted": accepted}, status=status.HTTP_200_OK)

        bids_by_cp = _bids_by_checkpoint(race_id)

        # De-dup the batch by id (keep last occurrence) — update_conflicts raises
        # CardinalityViolation on an in-batch duplicate conflict key. The real
        # client never sends dup ids, but a malformed body must 400/200, not 500.
        deduped = {m["id"]: m for m in marks}  # dict preserves last write

        mark_objs = []
        present_objs = []
        for m in deduped.values():
            loc = m.get("location") or {}
            mark_objs.append(
                Mark(
                    id=m["id"],
                    team_id=team_id,
                    race_id=race_id,
                    source_install_id=source_install_id,
                    checkpoint_id=m["checkpoint_id"],
                    method=m["method"],
                    cp_code=m["cp_code"],
                    cp_nfc_uid=m["cp_nfc_uid"],
                    expected_count=m["expected_count"],
                    complete=m["complete"],
                    verified=_is_verified(bids_by_cp, m["checkpoint_id"], m["cp_code"]),
                    trusted_ms=m.get("trusted_ms"),
                    wall_ms=m["wall_ms"],
                    elapsed_at=m.get("elapsed_at"),
                    boot_count=m.get("boot_count"),
                    loc_lat=loc.get("lat"),
                    loc_lon=loc.get("lon"),
                    loc_accuracy=loc.get("accuracy"),
                    loc_altitude=loc.get("altitude"),
                    loc_vertical_accuracy=loc.get("vertical_accuracy"),
                    loc_gps_time_ms=loc.get("gps_time_ms"),
                    loc_elapsed_at=loc.get("elapsed_at"),
                )
            )
            for member in m["present"]:
                present_objs.append(
                    MarkPresent(
                        mark_id=m["id"],
                        nfc_uid=member.get("nfc_uid"),
                        code=member.get("code"),
                        number=member["number"],
                        number_in_team=member["number_in_team"],
                    )
                )

        with transaction.atomic():
            # Parent upsert before child insert (FK ordering). Enrichment-merge:
            # a repeat id last-write-wins-overwrites the scalars; a new id inserts.
            Mark.objects.bulk_create(
                mark_objs,
                update_conflicts=True,
                unique_fields=["id"],
                update_fields=MARK_UPDATE_FIELDS,
            )
            # Additive: unique_together skips slots already stored, inserts the
            # rest (the late roster members of an enrichment re-send).
            MarkPresent.objects.bulk_create(present_objs, ignore_conflicts=True)

        return Response({"accepted": accepted}, status=status.HTTP_200_OK)


PHOTO_MAX_BYTES = 10 * 1024 * 1024  # 10 MB app-level cap (nginx gates at 50m)


class MarkPhotoUploadView(AppAPIView):
    """``POST /app/race/<race_id>/mark/<mark_id>/photo/<frame_id>`` — store one
    raw JPEG frame for a ``method="photo"`` :class:`Mark`.

    **Build-HMAC-only** like :class:`TrackUploadView`/:class:`MarkUploadView` —
    not part of the per-person write layer. The URL has **no trailing slash**
    (deliberately diverging from every other ``/app/`` route) because the
    signed canonical string is the request's ``full_path``, and the contract
    path (``UPLOAD.md``) ends at ``<frame_id>``.

    Gotcha #1: read ``request.body`` directly, never ``request.data`` — DRF's
    default parsers don't handle ``image/jpeg`` and touching ``.data`` 415s.
    ``SignedAppPermission`` already reads ``request.body`` (Django buffers it),
    so the HMAC signature over the raw bytes works unchanged. Gotcha #4: our
    responses carry no body, so a client ``Accept: image/jpeg`` would still
    406 in DRF's response content negotiation before the view runs — the
    Android client sends no such header.

    Idempotency mirrors the sibling batch endpoints but keyed by
    ``(mark, frame_id)`` instead of a client-UUID PK: a pre-check short-circuits
    a re-send to ``200``, and a concurrent duplicate that slips past the
    pre-check is caught via ``IntegrityError`` on ``unique_together`` — the row
    insert happens before the storage write inside the same atomic block, so
    the loser's ``IntegrityError`` fires before it ever touches storage — and
    is also acked ``200``.

    A frame arriving before its parent ``Mark`` row 404s. This is contract-safe:
    the client only starts draining a mark's frames after that mark's upload is
    already acknowledged, and it treats a photo ``404`` as transient — it
    retries on the next sync. No server-side hold/queue is needed.
    """

    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = "mobile-photo"

    # mark_id/frame_id are both interpolated into a filesystem path (see
    # _mark_photo_path); each must be a safe stem. mark_id is a client-chosen
    # Mark.id (no charset restriction at /marks/ ingestion), so a literal ".."
    # could otherwise reach Django's upload_to path-building unvalidated.
    SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

    def post(self, request, race_id, mark_id, frame_id):
        get_object_or_404(Race, pk=race_id, is_published=True)
        if not self.SAFE_ID_RE.fullmatch(mark_id) or not self.SAFE_ID_RE.fullmatch(
            frame_id
        ):
            return Response({"detail": "bad id"}, status=status.HTTP_400_BAD_REQUEST)
        mark = get_object_or_404(Mark, pk=mark_id, race_id=race_id)

        body = request.body  # raw JPEG — never touch request.data (415 on image/jpeg)
        if not body:
            return Response(
                {"detail": "empty body"}, status=status.HTTP_400_BAD_REQUEST
            )
        if len(body) > PHOTO_MAX_BYTES:
            return Response(
                {"detail": "too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )

        if MarkPhoto.objects.filter(mark_id=mark_id, frame_id=frame_id).exists():
            return Response(status=status.HTTP_200_OK)  # idempotent — already stored

        # Insert the (empty-image) row first, so the DB's unique_together is the
        # single arbiter of which concurrent request owns this (mark, frame_id) —
        # only the winner ever touches storage, so two racing requests can never
        # both write the canonical path (one overwriting the other's bytes). The
        # storage write stays inside the same atomic block: if it raises, the row
        # insert rolls back too, so a retry never finds a committed row with no
        # backing file (which would make the exists() pre-check ack a frame that
        # was never actually stored).
        photo = MarkPhoto(mark=mark, frame_id=frame_id)
        canonical = _mark_photo_path(photo, "")  # mark_photos/<mark_id>/<frame_id>.jpg
        try:
            with transaction.atomic():
                photo.save()
                # Reuse the deterministic name even if a crashed prior attempt
                # left an orphan canonical file at this path — safe because
                # we're now the sole owner of this row, so no other request
                # can be writing here too.
                if photo.image.storage.exists(canonical):
                    photo.image.storage.delete(canonical)
                photo.image.save(f"{frame_id}.jpg", ContentFile(body), save=True)
        except IntegrityError:
            # Could be the (mark, frame_id) unique constraint (expected
            # concurrent duplicate) or an unrelated failure. Re-query to
            # determine, mirroring TagCreateView's pattern.
            if not MarkPhoto.objects.filter(
                mark_id=mark_id, frame_id=frame_id
            ).exists():
                raise
            return Response(status=status.HTTP_200_OK)  # concurrent duplicate
        return Response(status=status.HTTP_201_CREATED)


class RaceListView(AppAPIView):
    """Return the list of published races for the mobile app (conditional GET).

    The ETag is the bare :func:`races_version` fingerprint wrapped in quotes; a
    matching ``If-None-Match`` short-circuits to an empty ``304`` before any
    serialization. The resource is global, so the fingerprint takes no
    ``race_id`` and is deliberately absent from the per-race ``/sync/`` manifest.
    """

    def get(self, request):
        quoted = f'"{races_version()}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        qs = Race.objects.filter(is_published=True)  # Meta.ordering = ["-date"]
        resp = Response({"races": RaceListSerializer(qs, many=True).data})
        resp["ETag"] = quoted
        return resp


class LegendView(AppAPIView):
    """Return the checkpoint legend (descriptions) for a race (conditional GET).

    The ETag is the bare :func:`legend_version` fingerprint wrapped in quotes;
    a matching ``If-None-Match`` short-circuits to an empty ``304`` before any
    serialization. The ETag is set on every exit path.
    """

    def get(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)
        # The legend is build-independent: the stored ciphertext/bundles do not
        # depend on the per-build secret, so the version folds in no key_id and
        # two builds share the ETag. Locked КП expose only their precomputed
        # `enc` blob; the app decrypts offline after scanning a tag's `code`.
        quoted = f'"{legend_version(race_id)}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        qs = (
            Checkpoint.objects.filter(race_id=race_id)
            .exclude(type=CheckpointType.hidden.value)
            .order_by("number", "id")
            .select_related("secret")
        )
        # Every tag — open and locked — carries identity (`bid → checkpoint_id`). The
        # locked-only `bundle_blob` is no longer a filter: open tags ride along
        # for offline cp_id recognition. `.exclude(bid="")` drops un-built tags
        # (created bypassing the build_bundle signal) that have no usable bid.
        tag_qs = (
            CheckpointTag.objects.filter(checkpoint__race_id=race_id)
            .exclude(checkpoint__type=CheckpointType.hidden.value)
            .exclude(bid="")
            .order_by("id")
        )
        # Progress-bar denominator: the sum of `cost` over the same non-hidden
        # КП the legend serves (open + locked). Sent in cleartext as an aggregate
        # only — the per-КП cost of a locked КП still never leaves the server, so
        # the legend lock (which hides strategic per-КП values) is preserved.
        # `Sum` ignores order_by/select_related; an empty race yields None → 0.
        # Derives purely from `Checkpoint.cost`, which `legend_version` already
        # fingerprints via `MAX(updated_at)|COUNT`, so no new ETag input is needed.
        # `scoring_count` = number of scoring КП (cost > 0) over the same set —
        # the progress-bar numerator's denominator (how many КП count toward score).
        agg = qs.aggregate(
            total=Sum("cost"),
            scoring=Count("id", filter=Q(cost__gt=0)),
        )
        total_cost = agg["total"] or 0
        scoring_count = agg["scoring"] or 0
        resp = Response(
            {
                "race": race_id,
                "total_cost": total_cost,
                "scoring_count": scoring_count,
                "checkpoints": LegendCheckpointSerializer(qs, many=True).data,
                "tags": TagSerializer(tag_qs, many=True).data,
            }
        )
        resp["ETag"] = quoted
        return resp


class TeamsView(AppAPIView):
    """Return a race's teams + members + categories (conditional GET).

    The response is ``{"race": id, "categories": [...], "teams": [...]}``. The
    ``categories`` block rides inside this resource (no separate endpoint) so the
    app can resolve a team's ``category2`` id into a label and build a filter; it
    lists **all** of the race's categories ordered ``order, id`` — including
    ``is_active=False`` ones, since a team may still reference a deactivated
    category (``is_active`` itself is not exposed).

    The ETag is the bare :func:`teams_version` fingerprint wrapped in quotes;
    category state is folded into that fingerprint, so a category rename/reorder
    or add/delete moves it too. A matching ``If-None-Match`` short-circuits to an
    empty ``304`` before any serialization of either queryset.
    """

    def get(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)
        quoted = f'"{teams_version(race_id)}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        categories = Category.objects.filter(race_id=race_id).order_by("order", "id")
        teams = (
            Team.objects.filter(category2__race_id=race_id)
            .order_by("id")
            .prefetch_related(
                Prefetch(
                    "athlet_set",
                    queryset=Athlet.objects.order_by("number_in_team", "id"),
                )
            )
        )
        resp = Response(
            {
                "race": race_id,
                "categories": CategorySerializer(categories, many=True).data,
                "teams": TeamSerializer(teams, many=True).data,
            }
        )
        resp["ETag"] = quoted
        return resp


class MemberTagsView(AppAPIView):
    """Return the member-tag (participant bracelet) pool for a race (conditional GET).

    The response is ``{"member_tags": [{number, nfc_uid}, ...]}`` — the offline
    ``nfc_uid → number`` identity the app uses to resolve a bracelet scan at a
    checkpoint. The served set is the data-anchored 30-day window from
    :func:`active_member_tags` (an idle pool is perfectly stable).

    The ETag is the bare :func:`member_tags_version` fingerprint wrapped in
    quotes; a matching ``If-None-Match`` short-circuits to an empty ``304``
    before any serialization. A scan (``touch``) cannot churn the ETag on its
    own — only provisioning edits (and day-scale window-membership shifts) move
    it (see ``member_tags_version``).

    ``race_id`` is the reserved (currently-unused) hook for a future per-race
    chip set: the pool is global today (``Tag`` has no race FK), so the id is
    validated for a published race but does not yet filter the pool.
    """

    def get(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)
        # Materialise once so the ETag and the response body come from the same
        # DB snapshot (a concurrent provisioning edit between two separate queries
        # would produce payload B with ETag A).
        tags = list(active_member_tags().order_by("id"))
        rows = [(t.id, t.number, t.nfc_uid) for t in tags]
        quoted = f'"{member_tags_version(rows)}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        resp = Response({"member_tags": MemberTagSerializer(tags, many=True).data})
        resp["ETag"] = quoted
        return resp


class SyncView(AppAPIView):
    """Return a pure version manifest for a race (no data serialization).

    A cheap signed probe: the client compares ``versions.teams`` / ``versions.legend``
    / ``versions.member_tags`` (the bare :func:`teams_version` / :func:`legend_version`
    / :func:`member_tags_version` fingerprints, the same values the ``/teams/`` /
    ``/legend/`` / ``/member_tags/`` ETags wrap in quotes) against its stored ETags to
    decide whether to re-fetch. No ``If-None-Match``/304 — there is nothing to
    short-circuit.

    Unlike ``races_version`` (global, deliberately absent here), ``member_tags`` is
    included even though it's a global pool: the member-tags endpoint is served at a
    per-race URL, so the app needs one sync poll to learn what to refetch for the race
    it's syncing.
    """

    def get(self, request, race_id):
        get_object_or_404(Race, pk=race_id, is_published=True)
        return Response(
            {
                "race": race_id,
                "data_source": settings.MOBILE_DATA_SOURCE,
                "lease_expires_at": None,
                "versions": {
                    "teams": teams_version(race_id),
                    # Build-independent: versions.legend equals the legend ETag
                    # for every build (stored ciphertext/bundles, no key_id).
                    "legend": legend_version(race_id),
                    # Global pool today, but served at a per-race URL — see the
                    # SyncView docstring for why it's in this per-race manifest.
                    "member_tags": member_tags_version(),
                },
            }
        )
