from django.urls import path, re_path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('index_hidden', views.index, name='index'),
    path('passlogin', views.passlogin, name='passlogin'),
    path('login', views.login, name='login'),
    re_path('^login/(?P<login_key>[0-9a-f]{16})', views.login_by_key),
    path('logout', views.logout_user, name='logout'),
    path('team', views.my_team, name='my_team'),
    path('teams', views.teams, name='teams'),
    re_path('^team/(?P<teamid>[0-9a-f]{16})', views.my_team),
    re_path('^success/(?P<teamid>[0-9a-f]{16})', views.success),
    path('newteam', views.new_team, name='new_team'),
    path('yandexinform', views.yandex_payment, name='yandexinform')
]
