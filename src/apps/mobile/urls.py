from django.urls import path

from .views import (
    LegendView,
    LoginView,
    LogoutView,
    MarkUploadView,
    MemberTagsView,
    RaceListView,
    SyncView,
    TagCreateView,
    TeamsView,
    TrackUploadView,
)

app_name = "mobile"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("races/", RaceListView.as_view(), name="races"),
    path("race/<int:race_id>/legend/", LegendView.as_view(), name="legend"),
    path("race/<int:race_id>/teams/", TeamsView.as_view(), name="teams"),
    path(
        "race/<int:race_id>/member_tags/",
        MemberTagsView.as_view(),
        name="member_tags",
    ),
    path("race/<int:race_id>/sync/", SyncView.as_view(), name="sync"),
    path(
        "race/<int:race_id>/tags/",
        TagCreateView.as_view(),
        name="tag_create",
    ),
    path(
        "race/<int:race_id>/track/",
        TrackUploadView.as_view(),
        name="track",
    ),
    path(
        "race/<int:race_id>/marks/",
        MarkUploadView.as_view(),
        name="marks",
    ),
]
