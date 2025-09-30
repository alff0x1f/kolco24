from rest_framework.generics import ListAPIView

from website.models import Checkpoint, Race
from website.models.enums import CheckpointType

from ..serializers import CheckpointSerializer


class CheckpointView(ListAPIView):
    """Эндпоинт для получения списка КП"""

    serializer_class = CheckpointSerializer

    def get_queryset(self):
        race_id = self.kwargs.get("race_id")
        return (
            Checkpoint.objects.filter(race_id=race_id)
            .exclude(type=CheckpointType.draft.value)
            .prefetch_related("tags")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        race = Race.objects.get(pk=self.kwargs.get("race_id"))
        context["is_legend_visible"] = race.is_legend_visible
        return context
