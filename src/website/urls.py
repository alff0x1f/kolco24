from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from apps.race.views import (
    PromoCheckView,
    ProtocolBuildView,
    ProtocolFreezeView,
    ProtocolView,
    RaceAppDataTeamView,
    RaceAppDataView,
    RaceEditView,
    RaceLegendCodesView,
    RaceLegendEditView,
    RaceMapMarksView,
    RaceMapPositionsView,
    RaceMapTrackView,
    RaceMapView,
    RacePageView,
    RaceTeamsView,
)

from . import views
from .views import (
    ArticleListView,
    HomeView,
    PublicationDetailView,
    RaceIdRedirectView,
    RaceListView,
)
from .views.team import EditTeamView, TeamCheckoutView, TeamMemberMoveView

urlpatterns = [
    path("", HomeView.as_view(), name="index"),
    path("articles/", ArticleListView.as_view(), name="article_list"),
    path(
        "post/<int:pk>/",
        PublicationDetailView.as_view(),
        name="publication_detail",
    ),
    path("races/", RaceListView.as_view(), name="race_list"),
    # auth now lives in apps.accounts (mounted at /accounts/ in config/urls.py)
    path("team/<int:team_id>/", EditTeamView.as_view(), name="edit_team"),
    path(
        "team/<int:team_id>/checkout/",
        TeamCheckoutView.as_view(),
        name="team_checkout",
    ),
    path(
        "team/<int:team_id>/move/",
        TeamMemberMoveView.as_view(),
        name="move_team_member",
    ),
    # ============================ Redirects ============================
    # Legacy int-id URLs → slug equivalents. MUST stay before the slug
    # patterns below (<slug:race_slug> also matches bare integers).
    path("race/<int:race_id>/", RaceIdRedirectView.as_view()),
    path("race/<int:race_id>/teams/", RaceIdRedirectView.as_view()),
    path("race/<int:race_id>/teams/my/", RaceIdRedirectView.as_view()),
    path("race/<int:race_id>/teams/add/", RaceIdRedirectView.as_view()),
    path(
        "race/<int:race_id>/category/<category_id>/teams/", RaceIdRedirectView.as_view()
    ),
    path(
        "race/<int:race_id>/category/<int:category_id>/results/",
        RaceIdRedirectView.as_view(),
    ),
    path("race/<int:race_id>/member_logs/", RaceIdRedirectView.as_view()),
    # Short-URL aliases → generic /page/<slug>/ view.
    path("privacy/", views.privacy_policy, name="privacy_policy"),
    path("refund_policy/", views.refund_policy, name="refund_policy"),
    path("service_order_rules/", views.service_order_rules, name="service_order_rules"),
    path("rules/", views.rules, name="rules"),
    path("contacts/", views.contacts, name="contacts"),
    # ========================== Working URLs ==========================
    path("races/new/", RaceEditView.as_view(), name="add_race"),
    # Slug-based (primary)
    path(
        "race/<slug:race_slug>/post/add/",
        views.AddNewsPostView.as_view(),
        name="add_post",
    ),
    path(
        "race/<slug:race_slug>/edit/",
        RaceEditView.as_view(),
        name="edit_race",
    ),
    path(
        "race/<slug:race_slug>/legend/edit/",
        RaceLegendEditView.as_view(),
        name="edit_legend",
    ),
    path(
        "race/<slug:race_slug>/legend/codes/",
        RaceLegendCodesView.as_view(),
        name="legend_codes",
    ),
    path(
        "race/<slug:race_slug>/map/",
        RaceMapView.as_view(),
        name="race_map",
    ),
    path(
        "race/<slug:race_slug>/map/positions/",
        RaceMapPositionsView.as_view(),
        name="race_map_positions",
    ),
    path(
        "race/<slug:race_slug>/map/track/<int:team_id>/",
        RaceMapTrackView.as_view(),
        name="race_map_track",
    ),
    path(
        "race/<slug:race_slug>/map/marks/",
        RaceMapMarksView.as_view(),
        name="race_map_marks",
    ),
    path(
        "race/<slug:race_slug>/app-data/",
        RaceAppDataView.as_view(),
        name="race_app_data",
    ),
    path(
        "race/<slug:race_slug>/app-data/team/<int:team_id>/",
        RaceAppDataTeamView.as_view(),
        name="race_app_data_team",
    ),
    path("race/<slug:race_slug>/", RacePageView.as_view(), name="race"),
    path("race/<slug:race_slug>/teams/", RaceTeamsView.as_view(), name="all_teams"),
    path(
        "race/<slug:race_slug>/teams/my/",
        RaceTeamsView.as_view(initial="mine"),
        name="my_teams",
    ),
    path("race/<slug:race_slug>/teams/add/", views.AddTeam.as_view(), name="add_team"),
    path(
        "race/<slug:race_slug>/promo/check/",
        PromoCheckView.as_view(),
        name="promo_check",
    ),
    path(
        "race/<slug:race_slug>/category/<category_id>/teams/",
        RaceTeamsView.as_view(),
        name="teams2",
    ),
    path(
        "race/<slug:race_slug>/category/<int:category_id>/results/",
        ProtocolView.as_view(),
        name="category_results",
    ),
    path(
        "race/<slug:race_slug>/category/<int:category_id>/results-deprecated/",
        views.AllTeamsResultView.as_view(),
        name="category_results_deprecated",
    ),
    path(
        "race/<slug:race_slug>/results/build/",
        ProtocolBuildView.as_view(),
        name="protocol_build",
    ),
    path(
        "race/<slug:race_slug>/results/freeze/",
        ProtocolFreezeView.as_view(),
        name="protocol_freeze",
    ),
    path(
        "race/<slug:race_slug>/member_logs/",
        views.TeamMemberRaceLogView.as_view(),
        name="race_member_logs",
    ),
    # path(
    #     "race/<race_id>/teams_result",
    #     views.AllTeamsResultView.as_view(),
    #     name="all_teams",
    # ),
    path(
        "team/<int:team_id>/points/", views.TeamPointsView.as_view(), name="team_points"
    ),
    path("regulations/", views.regulations, name="regulations"),
    path("page/<str:slug>/", views.page, name="page"),
    path("page/<str:slug>/edit/", views.edit_page, name="edit_page"),
    # path("newpoint/<int:pk>/", views.new_point, name="new_point"),
    # app api
    path("api/", include(("api.urls", "api"), namespace="api")),
    path(
        "api/race/<int:race_id>/upload_photo/",
        views.upload_photo,
        name="upload_photo",
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
