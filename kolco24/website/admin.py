from django.contrib import admin

from .models import Team, ControlPoint, TakenKP, PaymentsYa


class TeamAdmin(admin.ModelAdmin):
    list_display = ('id', 'paymentid', 'teamname',
                    'paid_people', 'dist', 'year')
    list_filter = ('year', 'category')


class ControlPointAdmin(admin.ModelAdmin):
    list_display = ('iterator', 'number', 'cost')
    list_filter = ('year', 'cost')


class TakenKPAdmin(admin.ModelAdmin):
    list_display = ('team', 'point')
    list_filter = ('team', 'point')


class PaymentsYaAdmin(admin.ModelAdmin):
    list_display = ('operation_id', 'amount', 'datetime', 'unaccepted')
    list_filter = ('datetime', 'amount')

admin.site.register(Team, TeamAdmin)
admin.site.register(ControlPoint, ControlPointAdmin)
admin.site.register(TakenKP, TakenKPAdmin)
admin.site.register(PaymentsYa, PaymentsYaAdmin)
