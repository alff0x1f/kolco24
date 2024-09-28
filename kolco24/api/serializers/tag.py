from rest_framework import serializers
from website.models import Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "number", "tag_id"]


class CheckpointTagSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    tag_id = serializers.CharField(max_length=255)
