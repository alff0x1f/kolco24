from rest_framework.generics import ListAPIView
from website.models import Team

from api.serializers.team import TeamSerializer


class TeamListView(ListAPIView):
    """Эндпоинт для получения списка Команд"""

    serializer_class = TeamSerializer

    def get_queryset(self):
        race_id = self.kwargs.get("race_id")
        return Team.objects.select_related("category2").filter(
            category2__race_id=race_id, paid_people__gt=0
        )
