from rest_framework import status
from rest_framework.generics import CreateAPIView, ListCreateAPIView
from rest_framework.response import Response
from website.models import PointTag, Tag

from ..serializers import TagSerializer
from ..serializers.tag import PointTagSerializer


class MemberTagListCreateView(ListCreateAPIView):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class PointTagsView(CreateAPIView):
    queryset = PointTag.objects.all()
    serializer_class = PointTagSerializer

    def create(self, request, *args, **kwargs):
        number = request.data.get("number")

        # Add number to the context so serializer can access it
        serializer = self.get_serializer(data=request.data, context={"number": number})

        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
