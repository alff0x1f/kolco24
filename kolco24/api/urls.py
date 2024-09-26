from django.http import HttpResponse
from django.urls import path

from api.views import MemberTagListCreateView

urlpatterns = [
    path(
        "ping/", lambda request: HttpResponse(content="[pong]", status=200), name="ping"
    ),
    path("member_tag/", MemberTagListCreateView.as_view(), name="tag-list-create"),
]
