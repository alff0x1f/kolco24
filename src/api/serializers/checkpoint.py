from rest_framework.fields import SerializerMethodField
from rest_framework.serializers import ModelSerializer

from website.models import Checkpoint
from website.models.enums import CheckpointType

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
        if (
            self.context.get("is_legend_visible")
            or checkpoint.type != CheckpointType.kp.value
        ):
            return checkpoint.description
        return ""

    def get_cost(self, checkpoint: Checkpoint) -> int:
        if (
            self.context.get("is_legend_visible")
            or checkpoint.type != CheckpointType.kp.value
        ):
            return checkpoint.cost
        return 0
