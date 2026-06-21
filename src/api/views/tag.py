from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from website.models import Checkpoint, CheckpointTag, Tag

from ..serializers import CheckpointTagSerializer, TagSerializer, TagTouchSerializer


class MemberTagListCreateView(ListCreateAPIView):
    """Эндпоинт для сохранения nfc тега участника (браслет)

    {
      "number": 1050,
      "nfc_uid": "045D7B32F31C90"
    }
    """

    queryset = Tag.objects.all()
    serializer_class = TagSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        return queryset.filter(last_seen_at__gte=thirty_days_ago)


class CheckpointTagCreateView(APIView):
    def post(self, request, race_id):
        """Эндпоинт для сохранения nfc тега КП"""
        if not self.request.user.is_superuser:
            # TODO выключено, тк эндпоинт пока не используется
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        serializer = CheckpointTagSerializer(data=request.data)
        if serializer.is_valid():
            number = serializer.validated_data.get("number")
            nfc_uid = serializer.validated_data.get("nfc_uid")

            control_point = self.get_control_point(race_id, number)
            # nfc_uid is globally unique (website migration 0089). If this UID is
            # already bound to a different КП, get_or_create's create() raises an
            # uncaught IntegrityError → 500; translate it to a clean 409 conflict.
            try:
                checkpoint_tag, created = CheckpointTag.objects.get_or_create(
                    point=control_point, nfc_uid=nfc_uid
                )
            except IntegrityError:
                return Response(
                    {"nfc_uid": ["Этот тег уже привязан к другому КП"]},
                    status=status.HTTP_409_CONFLICT,
                )

            return Response(
                {
                    "id": checkpoint_tag.id,
                    "point": checkpoint_tag.point.id,
                    "nfc_uid": checkpoint_tag.nfc_uid,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def get_control_point(race_id: int, number: int) -> Checkpoint:
        try:
            return Checkpoint.objects.get(race_id=race_id, number=number)
        except Checkpoint.DoesNotExist:
            raise NotFound(
                {"number": [f"Контрольная точка с номером {number} не найдена"]}
            )


class MemberTagTouchView(APIView):
    def post(self, request):
        serializer = TagTouchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        nfc_uid = serializer.validated_data["nfc_uid"]

        try:
            tag = Tag.objects.get(nfc_uid=nfc_uid)
        except Tag.DoesNotExist:
            raise NotFound({"nfc_uid": [f"Тег с UID {nfc_uid} не найден"]})

        tag.last_seen_at = timezone.now()
        # Intentionally omits "updated_at" — a scan (touch) must stay invisible to
        # the mobile member-tags fingerprint (apps/mobile/versioning.py:
        # member_tags_version), which hashes served field values (id, number, nfc_uid).
        # Deliberate carve-out from CLAUDE.md's "update_fields discipline": only
        # provisioning edits (add / renumber / remove) should move that fingerprint,
        # so a bracelet tap cannot churn the mobile ETag and trigger re-downloads
        # mid-race.
        tag.save(update_fields=["last_seen_at"])

        return Response(TagSerializer(tag).data, status=status.HTTP_200_OK)
