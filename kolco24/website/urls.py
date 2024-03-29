from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

from . import views
from .views import CancelPaymentView, ConfirmPaymentView

urlpatterns = [
    path("", views.index, name="index"),
    path("index_hidden", views.index, name="index"),
    path("passlogin", views.passlogin, name="passlogin"),
    path("login", views.login, name="login"),
    re_path("^login/(?P<login_key>[0-9a-f]{16})", views.login_by_key),
    path("logout", views.logout_user, name="logout"),
    path("team", views.my_team, name="my_team"),
    path("team_admin", views.team_admin, name="team_admin"),
    path("teams", views.teams, name="teams"),
    path("race/<race_id>/teams", views.AllTeamsView.as_view(), name="all_teams"),
    # path(
    #     "race/<race_id>/teams_result",
    #     views.AllTeamsResultView.as_view(),
    #     name="all_teams",
    # ),
    path("team/<team_id>/points", views.TeamPointsView.as_view(), name="all_teams"),
    path(
        "race/<race_id>/category/<category_id>/teams",
        views.TeamsView.as_view(),
        name="teams2",
    ),
    path(
        "race/<int:race_id>/category/<int:category_id>/results",
        views.AllTeamsResultView.as_view(),
        name="all_teams",
    ),
    path(
        "race/<race_id>/category/<category_id>/teams.csv",
        views.TeamsViewCsv.as_view(),
        name="teams2",
    ),
    path("teams_predstart", views.teams_predstart, name="teams_predstart"),
    path("teams_start", views.teams_start, name="teams_start"),
    path("teams_finish", views.teams_finish, name="teams_finish"),
    re_path("^team/(?P<teamid>[0-9a-f]{16})", views.my_team),
    re_path("^team_predstart/(?P<teamid>[0-9a-f]{16})", views.team_predstart),
    re_path("^team_start/(?P<teamid>[0-9a-f]{16})", views.team_start),
    re_path("^team_finish/(?P<teamid>[0-9a-f]{16})", views.team_finish),
    re_path("^success/(?P<teamid>[0-9a-f]{16})", views.success),
    path("newteam", views.new_team, name="new_team"),
    path("api/v1/newpayment", views.NewPaymentView.as_view(), name="new_payment"),
    path("api/v1/paymentinfo", views.paymentinfo, name="paymentinfo"),
    path("api/v1/getcost", views.get_cost, name="getcost"),
    path("yandexinform", views.yandex_payment, name="yandexinform"),
    path("sync_googledocs", views.sync_googledocs, name="sync_googledocs"),
    path("import_categories", views.import_categories, name="import_categories"),
    path("export_payments", views.export_payments, name="export_payments"),
    path("update_protocol", views.update_protocol, name="update_protocol"),
    path("upload_protocol", views.upload_protocol, name="upload_protocol"),
    path("regulations", views.regulations, name="regulations"),
    # admin
    path("payments", views.payment_list, name="payment-list"),
    path(
        "payments/confirm/<int:pk>",
        ConfirmPaymentView.as_view(),
        name="confirm-payment",
    ),
    path(
        "payments/cancel/<int:pk>",
        CancelPaymentView.as_view(),
        name="cancel-payment",
    ),
    path("payments/<int:pk>/up", views.PaymentUp.as_view(), name="payment-up"),
    path("payments/<int:pk>/down", views.PaymentDown.as_view(), name="payment-down"),
    # path("newpoint/<int:pk>/", views.new_point, name="new_point"),
    # app api
    path("api/v1/races", views.RaceView.as_view(), name="api_races"),
    path("api/v1/points", views.points, name="api_points"),
    path("api/v1/teams", views.teams_api, name="api_teams"),
    path("api/v1/teams/times", views.TeamsTimesView.as_view(), name="api_teams_times"),
    # path("api/v1/upload_photo", views.upload_photo, name="upload_photo"),
    # path(
    #     "api/v1/race/<int:race_id>/point_tags",
    #     views.PointTagsView.as_view(),
    #     name="point_tags",
    # ),
    path("cms/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("", include(wagtail_urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
