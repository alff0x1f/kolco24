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
from rest_framework.response import Response
from rest_framework.views import APIView

from website.models.checkpoint import Checkpoint
from website.models.enums import CheckpointType
from website.models.models import Athlet, Team
from website.models.race import Race

from .models import AppInstall
from .permissions import SignedAppPermission
from .serializers import LegendCheckpointSerializer, RaceListSerializer, TeamSerializer
from .versioning import legend_version, teams_version

logger = logging.getLogger(__name__)


class AppAPIView(APIView):
    """Base view for signed mobile-app endpoints: verify signature, record stats."""

    authentication_classes = []
    permission_classes = [SignedAppPermission]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # runs check_permissions
        self._record_install(request)

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
                    "last_ip": meta.get("ip"),
                },
            )
            AppInstall.objects.filter(install_id=install_id).update(
                request_count=F("request_count") + 1
            )
        except Exception:
            logger.exception("Failed to record AppInstall stats")


class RaceListView(AppAPIView):
    """Return the list of published races for the mobile app."""

    def get(self, request):
        qs = Race.objects.filter(is_published=True)  # Meta.ordering = ["-date"]
        return Response({"races": RaceListSerializer(qs, many=True).data})


class LegendView(AppAPIView):
    """Return the checkpoint legend (descriptions) for a race (conditional GET).

    The ETag is the bare :func:`legend_version` fingerprint wrapped in quotes;
    a matching ``If-None-Match`` short-circuits to an empty ``304`` before any
    serialization. The ETag is set on every exit path (incl. the
    ``is_legend_visible=False`` empty response) so a later un-hide is detected.
    """

    def get(self, request, race_id):
        race = get_object_or_404(Race, pk=race_id, is_published=True)
        quoted = f'"{legend_version(race_id)}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        if not race.is_legend_visible:
            resp = Response({"race": race_id, "checkpoints": []})
            resp["ETag"] = quoted
            return resp

        qs = (
            Checkpoint.objects.filter(race=race)
            .exclude(type=CheckpointType.draft.value)
            .order_by("number", "id")
        )
        resp = Response(
            {
                "race": race_id,
                "checkpoints": LegendCheckpointSerializer(qs, many=True).data,
            }
        )
        resp["ETag"] = quoted
        return resp


class TeamsView(AppAPIView):
    """Return a race's full team list with nested members (conditional GET).

    The ETag is the bare :func:`teams_version` fingerprint wrapped in quotes;
    a matching ``If-None-Match`` short-circuits to an empty ``304`` before any
    serialization.
    """

    def get(self, request, race_id):
        race = get_object_or_404(Race, pk=race_id, is_published=True)
        quoted = f'"{teams_version(race_id)}"'

        if request.headers.get("If-None-Match") == quoted:
            resp = HttpResponseNotModified()
            resp["ETag"] = quoted
            return resp

        teams = (
            Team.objects.filter(category2__race=race)
            .order_by("id")
            .prefetch_related(
                Prefetch(
                    "athlet_set",
                    queryset=Athlet.objects.order_by("number_in_team", "id"),
                )
            )
        )
        resp = Response(
            {"race": race_id, "teams": TeamSerializer(teams, many=True).data}
        )
        resp["ETag"] = quoted
        return resp


class SyncView(AppAPIView):
    """Return a pure version manifest for a race (no data serialization).

    A cheap signed probe: the client compares ``versions.teams`` / ``versions.legend``
    (the bare :func:`teams_version` / :func:`legend_version` fingerprints, the same
    values the ``/teams/`` and ``/legend/`` ETags wrap in quotes) against its stored
    ETags to decide whether to re-fetch. No ``If-None-Match``/304 — there is nothing
    to short-circuit.
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
                    "legend": legend_version(race_id),
                },
            }
        )
