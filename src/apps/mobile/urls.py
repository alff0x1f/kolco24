from django.urls import path

from .views import (
    LegendView,
    LoginView,
    LogoutView,
    MarkPhotoUploadView,
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
    # No trailing slash: the contract path (UPLOAD.md) ends at <frame_id>, and
    # the signed canonical string is the request's full_path, so the route must
    # match the client byte-for-byte — deliberate divergence from every other
    # /app/ endpoint's trailing-slash convention.
    path(
        "race/<int:race_id>/mark/<str:mark_id>/photo/<str:frame_id>",
        MarkPhotoUploadView.as_view(),
        name="mark_photo",
    ),
]
