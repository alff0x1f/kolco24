from django.urls import path

from apps.photo_contest import views

urlpatterns = [
    path("", views.PhotoContestListView.as_view(), name="photo_contests"),
    path(
        "entry/<int:entry_id>/original/",
        views.PhotoOriginalView.as_view(),
        name="photo_original",
    ),
    path("<slug:slug>/", views.PhotoContestView.as_view(), name="photo_contest"),
    path("<slug:slug>/upload/", views.PhotoUploadView.as_view(), name="photo_upload"),
    path(
        "<slug:slug>/moderators/",
        views.PhotoContestModeratorsView.as_view(),
        name="photo_contest_moderators",
    ),
    path(
        "<slug:slug>/moderate/",
        views.PhotoModerateView.as_view(),
        name="photo_moderate",
    ),
    path(
        "<slug:slug>/moderate/<int:entry_id>/",
        views.PhotoModerateEntryView.as_view(),
        name="photo_moderate_entry",
    ),
    path(
        "<slug:slug>/vote/<int:nomination_id>/",
        views.PhotoVoteView.as_view(),
        name="photo_vote",
    ),
    path(
        "<slug:slug>/vote/<int:nomination_id>/pair/",
        views.PhotoPairView.as_view(),
        name="photo_pair",
    ),
    path(
        "<slug:slug>/results/", views.PhotoResultsView.as_view(), name="photo_results"
    ),
    path(
        "<slug:slug>/results/publish/",
        views.PhotoResultsPublishView.as_view(),
        name="photo_results_publish",
    ),
    path("<slug:slug>/report/", views.PhotoReportView.as_view(), name="photo_report"),
    path(
        "<slug:slug>/report/void/",
        views.PhotoReportVoidView.as_view(),
        name="photo_report_void",
    ),
    path(
        "<slug:slug>/entry/<int:entry_id>/delete/",
        views.PhotoDeleteView.as_view(),
        name="photo_delete",
    ),
]
