from rest_framework.generics import ListCreateAPIView

from website.models import Tag

from ..serializers import TagSerializer


class MemberTagListCreateView(ListCreateAPIView):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
