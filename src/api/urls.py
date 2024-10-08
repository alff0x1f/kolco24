from django.http import HttpResponse
from django.urls import path

from api.views import (
    CheckpointTagCreateView,
    CheckpointView,
    MemberTagListCreateView,
    TeamCSVListView,
    TeamListView,
)

urlpatterns = [
    path(
        "ping/", lambda request: HttpResponse(content="[pong]", status=200), name="ping"
    ),
    path("member_tag/", MemberTagListCreateView.as_view(), name="tag-list-create"),
    path(
        "race/<int:race_id>/checkpoint/",
        CheckpointView.as_view(),
        name="race-checkpoints-list",
    ),
    path(
        "race/<int:race_id>/checkpoint_tag/",
        CheckpointTagCreateView.as_view(),
        name="checkpoint-tag-create",
    ),
    path("race/<int:race_id>/teams/", TeamListView.as_view(), name="team-list"),
    path(
        "race/<int:race_id>/teams.csv", TeamCSVListView.as_view(), name="team-list-csv"
    ),
]
