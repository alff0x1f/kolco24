from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from website.models import CheckpointTag, Checkpoint, Tag

from ..serializers import CheckpointTagSerializer, TagSerializer


class MemberTagListCreateView(ListCreateAPIView):
    """Эндпоинт для сохранения nfc тега участника (браслет)

    {
      "number": 1050,
      "tag_id": "045D7B32F31C90"
    }
    """

    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class CheckpointTagCreateView(APIView):
    def post(self, request, race_id):
        """Эндпоинт для сохранения nfc тега КП"""
        if not self.request.user.is_superuser:
            # TODO выключено, тк эндпоинт пока не используется
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        serializer = CheckpointTagSerializer(data=request.data)
        if serializer.is_valid():
            number = serializer.validated_data.get("number")
            tag_id = serializer.validated_data.get("tag_id")

            control_point = self.get_control_point(race_id, number)
            checkpoint_tag = CheckpointTag.objects.create(
                point=control_point, tag_id=tag_id
            )

            return Response(
                {
                    "id": checkpoint_tag.id,
                    "point": checkpoint_tag.point.id,
                    "tag_id": checkpoint_tag.tag_id,
                },
                status=status.HTTP_201_CREATED,
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
