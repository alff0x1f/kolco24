from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.urls import include, path, re_path

from apps.race.views import RaceEditView, RacePageView, RaceTeamsView

from . import views
from .views import CancelPaymentView, ConfirmPaymentView, RaceIdRedirectView
from .views.team import EditTeamView, TeamMemberMoveView

urlpatterns = [
    path("", lambda request: redirect("race", race_slug="kolco12-2025"), name="index"),
    # path("index_hidden/", views.IndexView.as_view(), name="index"),
    # auth now lives in apps.accounts (mounted at /accounts/ in config/urls.py)
    path("race/8/transfer/", views.TransferView.as_view(), name="transfer"),
    path(
        "race/8/transfer/list/",
        views.TransferPaidListView.as_view(),
        name="transfer_paid_list",
    ),
    path("team/", views.my_team, name="my_team"),
    path("team/<team_id>/", EditTeamView.as_view(), name="edit_team"),
    path("team/<team_id>/move/", TeamMemberMoveView.as_view(), name="move_team_member"),
    path("team/<team_id>/pay/", views.TeamPayment.as_view(), name="pay_team"),
    path("team_admin/", views.team_admin, name="team_admin"),
    path("teams/", views.teams, name="teams"),
    # Int-id redirects must come before slug patterns (slug matches ints too)
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
    path("race/<int:race_id>/breakfast/", RaceIdRedirectView.as_view()),
    path("race/<int:race_id>/breakfast/admin/", RaceIdRedirectView.as_view()),
    path("race/<int:race_id>/breakfast/list/", RaceIdRedirectView.as_view()),
    path("race/<int:race_id>/member_logs/", RaceIdRedirectView.as_view()),
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
    path("race/<slug:race_slug>/", RacePageView.as_view(), name="race"),
    path("race/<slug:race_slug>/teams/", RaceTeamsView.as_view(), name="all_teams"),
    path(
        "race/<slug:race_slug>/teams/my/",
        RaceTeamsView.as_view(initial="mine"),
        name="my_teams",
    ),
    path("race/<slug:race_slug>/teams/add/", views.AddTeam.as_view(), name="add_team"),
    path(
        "race/<slug:race_slug>/category/<category_id>/teams/",
        RaceTeamsView.as_view(),
        name="teams2",
    ),
    path(
        "race/<slug:race_slug>/category/<int:category_id>/results/",
        views.AllTeamsResultView.as_view(),
        name="category_results",
    ),
    path(
        "race/<slug:race_slug>/member_logs/",
        views.TeamMemberRaceLogView.as_view(),
        name="race_member_logs",
    ),
    path(
        "race/<slug:race_slug>/breakfast/",
        views.BreakfastView.as_view(),
        name="breakfast",
    ),
    path(
        "race/<slug:race_slug>/breakfast/admin/",
        views.BreakfastAdminView.as_view(),
        name="breakfast_admin",
    ),
    path(
        "race/<slug:race_slug>/breakfast/list/",
        views.BreakfastPaidListView.as_view(),
        name="breakfast_paid_list",
    ),
    # path(
    #     "race/<race_id>/teams_result",
    #     views.AllTeamsResultView.as_view(),
    #     name="all_teams",
    # ),
    path("team/<team_id>/points/", views.TeamPointsView.as_view(), name="team_points"),
    # path("teams_predstart/", views.teams_predstart, name="teams_predstart"),
    # path("teams_start/", views.teams_start, name="teams_start"),
    # path("teams_finish/", views.teams_finish, name="teams_finish"),
    re_path("^team/(?P<teamid>[0-9a-f]{16})/", views.my_team),
    re_path("^team_predstart/(?P<teamid>[0-9a-f]{16})/", views.team_predstart),
    re_path("^team_start/(?P<teamid>[0-9a-f]{16})/", views.team_start),
    re_path("^team_finish/(?P<teamid>[0-9a-f]{16})/", views.team_finish),
    re_path("^success/(?P<teamid>[0-9a-f]{16})/", views.success),
    path("newteam/", views.new_team, name="new_team"),
    path("api/v1/newpayment/", views.NewPaymentView.as_view(), name="new_payment"),
    path("api/v1/paymentinfo/", views.paymentinfo, name="paymentinfo"),
    path("api/v1/getcost/", views.get_cost, name="getcost"),
    path("yandexinform/", views.yandex_payment, name="yandexinform"),
    path("update_protocol/", views.update_protocol, name="update_protocol"),
    path("upload_protocol/", views.upload_protocol, name="upload_protocol"),
    path("regulations/", views.regulations, name="regulations"),
    path("privacy/", views.privacy_policy, name="privacy_policy"),
    path("refund_policy/", views.refund_policy, name="refund_policy"),
    path("service_order_rules/", views.service_order_rules, name="service_order_rules"),
    path("rules/", views.rules, name="rules"),
    path("contacts/", views.contacts, name="contacts"),
    path("page/<str:slug>/", views.page, name="page"),
    path("page/<str:slug>/edit/", views.edit_page, name="edit_page"),
    # admin
    path("payments/", views.payment_list, name="payment-list"),
    path(
        "payments/confirm/<int:pk>/",
        ConfirmPaymentView.as_view(),
        name="confirm-payment",
    ),
    path(
        "payments/cancel/<int:pk>/",
        CancelPaymentView.as_view(),
        name="cancel-payment",
    ),
    path("payments/<int:pk>/up/", views.PaymentUp.as_view(), name="payment-up"),
    path("payments/<int:pk>/down/", views.PaymentDown.as_view(), name="payment-down"),
    # path("newpoint/<int:pk>/", views.new_point, name="new_point"),
    # app api
    path("api/", include(("api.urls", "api"), namespace="api")),
    path("api/v1/races/", views.RaceView.as_view(), name="api_races"),
    path("api/v1/teams/", views.teams_api, name="api_teams"),
    path("api/v1/teams/times/", views.TeamsTimesView.as_view(), name="api_teams_times"),
    path(
        "api/race/<int:race_id>/upload_photo/",
        views.upload_photo,
        name="upload_photo",
    ),
    # path(
    #     "api/v1/race/<int:race_id>/point_tags",
    #     views.PointTagsView.as_view(),
    #     name="point_tags",
    # ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
