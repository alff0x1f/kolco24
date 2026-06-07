from django.urls import path

from .views import LegendView, RaceListView

app_name = "mobile"

urlpatterns = [
    path("races/", RaceListView.as_view(), name="races"),
    path("race/<int:race_id>/legend/", LegendView.as_view(), name="legend"),
]
