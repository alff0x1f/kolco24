from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.core import urls as wagtail_urls
from wagtail.documents import urls as wagtaildocs_urls

from . import views

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
    path("teams_predstart", views.teams_predstart, name="teams_predstart"),
    path("teams_start", views.teams_start, name="teams_start"),
    path("teams_finish", views.teams_finish, name="teams_finish"),
    re_path("^team/(?P<teamid>[0-9a-f]{16})", views.my_team),
    re_path("^team_predstart/(?P<teamid>[0-9a-f]{16})", views.team_predstart),
    re_path("^team_start/(?P<teamid>[0-9a-f]{16})", views.team_start),
    re_path("^team_finish/(?P<teamid>[0-9a-f]{16})", views.team_finish),
    re_path("^success/(?P<teamid>[0-9a-f]{16})", views.success),
    path("newteam", views.new_team, name="new_team"),
    path("api/v1/newpayment", views.new_payment, name="new_payment"),
    path("api/v1/paymentinfo", views.paymentinfo, name="paymentinfo"),
    path("api/v1/getcost", views.get_cost, name="getcost"),
    path("yandexinform", views.yandex_payment, name="yandexinform"),
    path("sync_googledocs", views.sync_googledocs, name="sync_googledocs"),
    path("import_categories", views.import_categories, name="import_categories"),
    path("export_payments", views.export_payments, name="export_payments"),
    path("update_protocol", views.update_protocol, name="update_protocol"),
    path("upload_protocol", views.upload_protocol, name="upload_protocol"),
    path("regulations", views.regulations, name="regulations"),
    path("api/v1/points", views.points, name="points"),
    path("cms/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("", include(wagtail_urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
