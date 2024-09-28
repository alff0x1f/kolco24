from api.views import CheckpointTagCreateView, MemberTagListCreateView
from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path(
        "ping/", lambda request: HttpResponse(content="[pong]", status=200), name="ping"
    ),
    path("member_tag/", MemberTagListCreateView.as_view(), name="tag-list-create"),
    path(
        "race/<int:race_id>/checkpoint_tag/",
        CheckpointTagCreateView.as_view(),
        name="checkpoint-tag-create",
    ),
]
