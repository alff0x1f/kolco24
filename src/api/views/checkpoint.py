from rest_framework.generics import ListAPIView

from website.models import Checkpoint
from website.models.enums import CheckpointType

from ..serializers import CheckpointSerializer


class CheckpointView(ListAPIView):
    """Эндпоинт для получения списка КП"""

    serializer_class = CheckpointSerializer

    def get_queryset(self):
        race_id = self.kwargs.get("race_id")
        return (
            Checkpoint.objects.filter(race_id=race_id)
            .exclude(type=CheckpointType.hidden.value)
            .prefetch_related("tags")
        )
