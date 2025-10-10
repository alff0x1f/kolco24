from rest_framework import serializers

from website.models import Team


class TeamSerializer(serializers.ModelSerializer):
    """Сериализатор для команды"""

    category = serializers.IntegerField(source="category2_id")

    class Meta:
        model = Team
        fields = [
            "id",
            "teamname",
            "paid_people",
            "ucount",
            "category",
            "start_number",
            "start_time",
            "finish_time",
            "athlet1",
            "athlet2",
            "athlet3",
            "athlet4",
            "athlet5",
            "athlet6",
        ]


class TeamStartSerializer(serializers.Serializer):
    team_id = serializers.IntegerField()
    start_number = serializers.CharField(max_length=50, allow_blank=True)
    team_name = serializers.CharField(max_length=255, allow_blank=True)
    participant_count = serializers.IntegerField(min_value=0)
    scanned_count = serializers.IntegerField(min_value=0)
    member_tags = serializers.ListField(
        child=serializers.CharField(max_length=64), allow_empty=True
    )
    start_timestamp = serializers.IntegerField()


class TeamFinishSerializer(serializers.Serializer):
    member_tag_id = serializers.IntegerField()
    tag_uid = serializers.CharField(max_length=64)
    recorded_at = serializers.IntegerField()

    def validate_tag_uid(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Tag UID не может быть пустым")
        return value
