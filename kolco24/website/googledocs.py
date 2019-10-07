import gspread
from oauth2client.service_account import ServiceAccountCredentials
from website.models import PaymentsYa, Team
from datetime import timedelta
from django.conf import settings

def connect_to_sheet(sheet_number=0, tablekey=settings.GOOGLE_DOCS_KEY):
    scope = ['https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive']
    credentials = ServiceAccountCredentials.from_json_keyfile_name('./kolco24/googledocs_api_key.json', scope)

    gc = gspread.authorize(credentials)
    sht1 = gc.open_by_key(tablekey)
    return sht1.get_worksheet(sheet_number)

def import_start_numbers_from_sheet():
    wks = connect_to_sheet()
    teams_ids = wks.col_values(2)
    start_numbers = wks.col_values(3)
    min_col = len(teams_ids) if len(teams_ids) < len(start_numbers) else len(start_numbers)

    updated_count = 0

    for i in range(min_col - 1):
        team_id = teams_ids[i + 1]
        team_sn = start_numbers[i + 1]
        if team_id:
            team = Team.objects.filter(id=team_id)[:1]
            if team and team_sn:
                team = team.get()
                if team.start_number != team_sn:
                    team.start_number = team_sn
                    team.save()
                    updated_count += 1

    return updated_count

def import_category_from_sheet():
    wks = connect_to_sheet()
    teams_ids = wks.col_values(2)
    categories = wks.col_values(5)
    min_col = len(teams_ids) if len(teams_ids) < len(categories) else len(categories)

    updated_count = 0

    for i in range(min_col - 1):
        team_id = teams_ids[i + 1]
        team_cat = categories[i + 1]
        if team_id:
            team = Team.objects.filter(id=team_id)[:1]
            if team and team_cat:
                team = team.get()
                if team.category != team_cat:
                    team.category = team_cat
                    team.save()
                    updated_count += 1

    return updated_count

def export_payments_to_sheet():
    wks = connect_to_sheet(1)
    payments = PaymentsYa.objects.filter(unaccepted=False)
    fields_count = 9
    updated_count = 0

    insert_range = wks.range(3, 1, len(payments) + 3, fields_count)
    i = 0
    for payment in payments:
        insert_range[i].value = payment.id
        insert_range[i + 1].value = payment.notification_type
        insert_range[i + 2].value = payment.amount.replace('.', ',')
        insert_range[i + 3].value = payment.withdraw_amount.replace('.', ',')
        insert_range[i + 4].value = payment.datetime
        insert_range[i + 5].value = payment.sender
        team = Team.objects.filter(paymentid=payment.label)
        if team:
            team = team.get()
            insert_range[i + 6].value = team.id
            insert_range[i + 7].value = team.teamname
            insert_range[i + 8].value = "%s %s" % (team.owner.first_name, 
                                                   team.owner.last_name)
        i += fields_count
        updated_count += 1
    wks.update_cells(insert_range)

    return updated_count

def sync_sheet(googlekey=""):
    fields_count = 35
    wks = connect_to_sheet(tablekey=googlekey)
    colA = wks.col_values(1)

    hide_unpaid = False
    if len(colA) > 0:
        A1 = colA[0]
        print(A1)
        if 'hide' in A1:
            hide_unpaid = True

    teams_ids = wks.col_values(2)
    if len(teams_ids) <= 1:
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
        payment = PaymentsYa()
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
                team_info.append(team.organization)
                team_info.append(team.city)
                team_info.append(team.owner.profile.phone)
                team_info.append(team.owner.email)
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