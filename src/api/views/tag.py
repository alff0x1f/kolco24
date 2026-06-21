from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mobile.legend_crypto import build_bundle
from website.models import Checkpoint, CheckpointTag, Tag
from website.models.enums import CheckpointType

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
            checkpoint_id = serializer.validated_data.get("checkpoint_id")
            nfc_uid = serializer.validated_data.get("nfc_uid")

            control_point = self.get_control_point(race_id, checkpoint_id)
            # nfc_uid is globally unique (website migration 0089). If this UID is
            # already bound to a different КП, get_or_create's create() raises an
            # IntegrityError; re-query to confirm it's a UID conflict and not an
            # unrelated failure (FK violation, crypto signal, etc.) before 409.
            try:
                checkpoint_tag, created = CheckpointTag.objects.get_or_create(
                    checkpoint=control_point, nfc_uid=nfc_uid
                )
            except IntegrityError as original_exc:
                conflict = (
                    CheckpointTag.objects.filter(nfc_uid=nfc_uid)
                    .exclude(checkpoint=control_point)
                    .exists()
                )
                if conflict:
                    return Response(
                        {"nfc_uid": ["Этот тег уже привязан к другому КП"]},
                        status=status.HTTP_409_CONFLICT,
                    )
                raise original_exc

            # The post_save signal writes bid/code/bundle_blob to a freshly-fetched
            # DB instance, leaving the get_or_create return value stale on creation.
            checkpoint_tag.refresh_from_db()

            # Existing rows created bypassing the signals (bid=="" / code is None)
            # must be repaired before attempting code.hex(). Mirror mobile
            # TagCreateView._tag_response(): re-fetch under a lock so concurrent
            # repairs can't mint different codes.
            if not checkpoint_tag.bid or checkpoint_tag.code is None:
                with transaction.atomic():
                    checkpoint_tag = CheckpointTag.objects.select_for_update().get(
                        pk=checkpoint_tag.pk
                    )
                    if not checkpoint_tag.bid or checkpoint_tag.code is None:
                        build_bundle(checkpoint_tag)
                        checkpoint_tag.refresh_from_db()

            code = (
                bytes(checkpoint_tag.code) if checkpoint_tag.code is not None else None
            )
            return Response(
                {
                    "bid": checkpoint_tag.bid,
                    "checkpoint_id": checkpoint_tag.checkpoint_id,
                    "number": checkpoint_tag.checkpoint.number,
                    "nfc_uid": checkpoint_tag.nfc_uid,
                    "code": code.hex() if code is not None else None,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def get_control_point(race_id: int, checkpoint_id: int) -> Checkpoint:
        # Resolve by id (number is not unique per race) and never bind a tag to a
        # hidden КП. 404 on miss, matching the mobile tag-create endpoint.
        try:
            return Checkpoint.objects.exclude(type=CheckpointType.hidden.value).get(
                race_id=race_id, id=checkpoint_id
            )
        except Checkpoint.DoesNotExist:
            raise NotFound(
                {
                    "checkpoint_id": [
                        f"Контрольная точка с id {checkpoint_id} не найдена"
                    ]
                }
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
