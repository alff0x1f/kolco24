from django.urls import path, re_path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('index_hidden', views.index, name='index'),
    path('login', views.login, name='login'),
    path('logout', views.logout_user, name='logout'),
    path('team', views.my_team, name='my_team'),
    path('teams', views.teams, name='teams'),
    re_path('^team/(?P<teamid>[0-9a-f]{16})', views.my_team),
    path('newteam', views.new_team, name='new_team'),
    path('yandexinform', views.yandex_payment, name='yandexinform')
]
