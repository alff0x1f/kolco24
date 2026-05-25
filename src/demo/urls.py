from django.urls import path
from django.views.generic import TemplateView

urlpatterns = [
    path(
        "home-multiple/",
        TemplateView.as_view(template_name="demo/home-multiple.html"),
        name="demo-home-multiple",
    ),
    path(
        "home-offseason/",
        TemplateView.as_view(template_name="demo/home-offseason.html"),
        name="demo-home-offseason",
    ),
    path(
        "home-single/",
        TemplateView.as_view(template_name="demo/home-single.html"),
        name="demo-home-single",
    ),
]
