from rest_framework import serializers
from website.models import CheckpointTag, Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "number", "tag_id"]


class CheckpointTagSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    tag_id = serializers.CharField(max_length=255)


class CheckpointTagSerializer2(serializers.ModelSerializer):
    class Meta:
        model = CheckpointTag
        fields = ["id", "tag_id", "check_method"]
