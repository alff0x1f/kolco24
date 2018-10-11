import gspread
from oauth2client.service_account import ServiceAccountCredentials
from website.models import Payments, Team
from datetime import timedelta
from django.conf import settings

def connect_to_sheet():
    scope = ['https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive']
    credentials = ServiceAccountCredentials.from_json_keyfile_name('./kolco24/googledocs_api_key.json', scope)

    gc = gspread.authorize(credentials)
    sht1 = gc.open_by_key(settings.GOOGLE_DOCS_KEY)
    return sht1.sheet1

def sync_sheet():
    fields_count = 35
    wks = connect_to_sheet()
    colA = wks.col_values(1)

    hide_unpaid = False
    if len(colA) > 0:
        A1 = colA[0]
        print(A1)
        if 'hide' in A1:
            hide_unpaid = True

    teams_ids = wks.col_values(2)
    if len(teams_ids) == 1:
        return

    insert_range = wks.range(2, 4, len(teams_ids), 3 + fields_count)
    team_info = []
    for team in teams_ids[1:]:
        team_info += get_team_info(team, fields_count, hide_unpaid=hide_unpaid)
    for i in range(len(team_info)):
        insert_range[i].value = team_info[i]
    wks.update_cells(insert_range)

def get_team_info(team_id, fields_count, hide_unpaid = False):
    team_info = []
    if team_id:
        team = Team.objects.filter(id=team_id)[:1]
        payment = Payments()
        if team:
            team = team.get()
            if team.paid_sum or not hide_unpaid:
                team_info.append(team.dist)
                team_info.append(team.category)
                team_info.append(team.ucount)
                team_info.append(team.paid_people)
                team_info.append(team.paid_sum)
                team_info.append(payment.get_sum(team.paymentid))
                team_info.append(team.teamname)
                team_info.append(team.owner.email)
                team_info.append(team.owner.profile.phone)
                team_info.append(team.city)
                team_info.append(team.organization)
                team_info.append(team.owner.last_name + ' ' + team.owner.first_name)
                team_info.append(team.athlet1)
                team_info.append(team.birth1 if team.birth1 != 0 else '')
                team_info.append(team.athlet2)
                team_info.append(team.birth2 if team.birth2 != 0 else '')
                team_info.append(team.athlet3)
                team_info.append(team.birth3 if team.birth3 != 0 else '')
                team_info.append(team.athlet4)
                team_info.append(team.birth4 if team.birth4 != 0 else '')
                team_info.append(team.athlet5)
                team_info.append(team.birth5 if team.birth5 != 0 else '')
                team_info.append(team.athlet6)
                team_info.append(team.birth6 if team.birth6 != 0 else '')
                team_info.append((team.created_at+timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S'))
                team_info.append((team.updated_at+timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S'))
                team_info.append((team.start_time+timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S') if team.start_time else '')
                team_info.append((team.finish_time+timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S') if team.finish_time else '')
                team_info.append(team.distance_time)
                team_info.append(team.penalty)
                team_info.append('Снятие' if team.dnf else '')
                team_info.append('Получил пакет' if team.get_package else '')
                team_info.append('Номер' if team.get_number else '')
                team_info.append('Сдал завку' if team.give_paper else '')
                team_info.append('Фото сдал' if team.give_paper else '')

    return team_info[:fields_count] if team_info else [''] * fields_count