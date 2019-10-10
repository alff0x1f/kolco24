from django.contrib import admin

from .models import Team, ControlPoint


class TeamAdmin(admin.ModelAdmin):
    list_display = ('id', 'start_number', 'teamname', 'paid_people', 'dist','year')
    list_filter = ('year', 'category')


admin.site.register(Team, TeamAdmin)
admin.site.register(ControlPoint)
