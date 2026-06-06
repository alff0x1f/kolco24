from django.urls import path

from .views import LegendView

app_name = "mobile"

urlpatterns = [
    path("race/<int:race_id>/legend/", LegendView.as_view(), name="legend"),
]
