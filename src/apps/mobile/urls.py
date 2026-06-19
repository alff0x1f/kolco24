from django.urls import path

from .views import LegendView, MemberTagsView, RaceListView, SyncView, TeamsView

app_name = "mobile"

urlpatterns = [
    path("races/", RaceListView.as_view(), name="races"),
    path("race/<int:race_id>/legend/", LegendView.as_view(), name="legend"),
    path("race/<int:race_id>/teams/", TeamsView.as_view(), name="teams"),
    path(
        "race/<int:race_id>/member_tags/",
        MemberTagsView.as_view(),
        name="member_tags",
    ),
    path("race/<int:race_id>/sync/", SyncView.as_view(), name="sync"),
]
