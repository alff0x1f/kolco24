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
