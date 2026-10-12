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
        "<slug:slug>/entry/<int:entry_id>/delete/",
        views.PhotoDeleteView.as_view(),
        name="photo_delete",
    ),
]
