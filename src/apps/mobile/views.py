"""Mobile-app API views.

All views are gated by :class:`SignedAppPermission` (HMAC signature over a
canonical string). The base :class:`AppAPIView` records per-install usage stats
in :meth:`initial` — *after* permissions pass — and never lets a stats-write
failure break the response.
"""

import logging

from django.conf import settings
from django.db.models import F, Prefetch
from django.http import HttpResponseNotModified
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from website.models.checkpoint import Checkpoint, CheckpointTag
from website.models.enums import CheckpointType
from website.models.models import Athlet, Team
from website.models.race import Category, Race

from .models import AppAuthFailure, AppInstall
from .permissions import SignedAppPermission
from .serializers import (
    CategorySerializer,
    LegendCheckpointSerializer,
    MemberTagSerializer,
    RaceListSerializer,
    TagSerializer,
    TeamSerializer,
)
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
        """Best-effort 403 log + aggregated DB row — must never break the 403."""
        d = getattr(request, "app_denial", {"reason": "unknown"})
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
        # Every tag — open and locked — carries identity (`bid → point`). The
        # locked-only `bundle_blob` is no longer a filter: open tags ride along
        # for offline cp_id recognition. `.exclude(bid="")` drops un-built tags
        # (created bypassing the build_bundle signal) that have no usable bid.
        tag_qs = (
            CheckpointTag.objects.filter(point__race_id=race_id)
            .exclude(point__type=CheckpointType.hidden.value)
            .exclude(bid="")
            .order_by("id")
        )
        resp = Response(
            {
                "race": race_id,
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
        quoted = f'"{member_tags_version()}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        resp = Response(
            {"member_tags": MemberTagSerializer(active_member_tags(), many=True).data}
        )
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
