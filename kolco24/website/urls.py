from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('index_hidden', views.index, name='index'),
    path('login', views.login, name='login'),
    path('logout', views.logout_user, name='logout'),
    path('team', views.my_team, name='my_team'),
    path('newteam', views.new_team, name='new_team'),
    path('yandexinform', views.yandex_payment, name='yandexinform')
]
