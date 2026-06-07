"""Mobile-app API views.

All views are gated by :class:`SignedAppPermission` (HMAC signature over a
canonical string). The base :class:`AppAPIView` records per-install usage stats
in :meth:`initial` — *after* permissions pass — and never lets a stats-write
failure break the response.
"""

import logging

from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from website.models.checkpoint import Checkpoint
from website.models.enums import CheckpointType
from website.models.race import Race

from .models import AppInstall
from .permissions import SignedAppPermission
from .serializers import LegendCheckpointSerializer, RaceListSerializer

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
    """Return the checkpoint legend (descriptions) for a race."""

    def get(self, request, race_id):
        race = get_object_or_404(Race, pk=race_id)
        if not race.is_legend_visible:
            return Response({"race": race_id, "checkpoints": []})
        qs = (
            Checkpoint.objects.filter(race=race)
            .exclude(type=CheckpointType.draft.value)
            .order_by("number", "id")
        )
        return Response(
            {
                "race": race_id,
                "checkpoints": LegendCheckpointSerializer(qs, many=True).data,
            }
        )
