from rest_framework import serializers

from website.models import CheckpointTag, Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "number", "nfc_uid", "last_seen_at"]


class TagTouchSerializer(serializers.Serializer):
    nfc_uid = serializers.CharField(max_length=255)


class CheckpointTagSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    nfc_uid = serializers.CharField(max_length=255)


class CheckpointTagSerializer2(serializers.ModelSerializer):
    class Meta:
        model = CheckpointTag
        fields = ["id", "nfc_uid", "check_method"]
