from rest_framework import serializers
from website.models import Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "number", "tag_id"]
