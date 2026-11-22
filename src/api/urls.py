from django.http import HttpResponse
from django.urls import path

from api.views import ContributorsView

urlpatterns = [
    path(
        "ping/", lambda request: HttpResponse(content="[pong]", status=200), name="ping"
    ),
    path("contributors/", ContributorsView.as_view(), name="contributors"),
]
