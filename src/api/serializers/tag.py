from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from website.models import CheckpointTag, Tag


class _NfcUidField(serializers.CharField):
    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().upper()


class TagSerializer(serializers.ModelSerializer):
    nfc_uid = _NfcUidField(
        max_length=255,
        validators=[UniqueValidator(queryset=Tag.objects.all())],
    )

    class Meta:
        model = Tag
        fields = ["id", "number", "nfc_uid", "last_seen_at"]


class TagTouchSerializer(serializers.Serializer):
    nfc_uid = _NfcUidField(max_length=255)


class CheckpointTagSerializer(serializers.Serializer):
    checkpoint_id = serializers.IntegerField()
    nfc_uid = _NfcUidField(max_length=255)


class CheckpointTagSerializer2(serializers.ModelSerializer):
    class Meta:
        model = CheckpointTag
        fields = ["id", "nfc_uid", "check_method"]
