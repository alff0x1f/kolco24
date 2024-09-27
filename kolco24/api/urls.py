from api.views import MemberTagListCreateView, PointTagsView
from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path(
        "ping/", lambda request: HttpResponse(content="[pong]", status=200), name="ping"
    ),
    path("member_tag/", MemberTagListCreateView.as_view(), name="tag-list-create"),
    path(
        "race/<int:race_id>/point_tags/",
        PointTagsView.as_view(),
        name="point_tags2",
    ),
]
