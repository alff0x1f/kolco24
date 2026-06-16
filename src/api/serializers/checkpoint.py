from rest_framework.fields import SerializerMethodField
from rest_framework.serializers import ModelSerializer

from website.models import Checkpoint

from .tag import CheckpointTagSerializer2


class CheckpointSerializer(ModelSerializer):
    """Сериализатор для КП"""

    description = SerializerMethodField()
    cost = SerializerMethodField()
    tags = CheckpointTagSerializer2(many=True)

    class Meta:
        model = Checkpoint
        fields = ("id", "number", "cost", "description", "type", "tags")

    def get_description(self, checkpoint: Checkpoint) -> str:
        return "" if checkpoint.is_legend_locked else checkpoint.description

    def get_cost(self, checkpoint: Checkpoint) -> int:
        return 0 if checkpoint.is_legend_locked else checkpoint.cost
