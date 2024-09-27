from rest_framework import serializers
from website.models import ControlPoint, PointTag, Race, Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "number", "tag_id"]


class PointTagSerializer(serializers.ModelSerializer):
    race = serializers.PrimaryKeyRelatedField(queryset=Race.objects.all())
    point = serializers.PrimaryKeyRelatedField(
        queryset=ControlPoint.objects.all(), required=False
    )

    class Meta:
        model = PointTag
        fields = ["race", "point", "tag_id"]

    def create(self, validated_data):
        number = self.context["number"]
        control_point = ControlPoint.objects.get(
            number=number, race=validated_data["race"]
        )
        validated_data["point"] = control_point
        return super().create(validated_data)
