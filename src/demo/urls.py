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
    path(
        "team-register/",
        TemplateView.as_view(template_name="demo/team-register.html"),
        name="demo-team-register",
    ),
    # Error-page previews — render the real 404/403/500 templates directly
    # (returns HTTP 200) so the design is viewable without DEBUG=off + a real error.
    path(
        "404/",
        TemplateView.as_view(template_name="404.html"),
        name="demo-404",
    ),
    path(
        "403/",
        TemplateView.as_view(template_name="403.html"),
        name="demo-403",
    ),
    path(
        "500/",
        TemplateView.as_view(template_name="500.html"),
        name="demo-500",
    ),
]
