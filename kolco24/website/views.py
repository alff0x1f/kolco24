from datetime import datetime, timezone, timedelta
from time import time, gmtime, strftime
from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from website.forms import (LoginForm, FastLoginForm, RegForm, TeamForm,
                           TeamFormAdmin, Export2GoogleDocsForm)
from website.models import (PaymentsYa, Team, Athlet, Payment, PaymentLog,
                            FastLogin)
from django.http import HttpResponseRedirect, Http404, JsonResponse
from django.conf import settings
from website.email import send_login_email
from website.googledocs import (export_teams,
                                export_teams_pretty,
                                import_start_numbers_from_sheet,
                                import_category_from_sheet,
                                export_payments_to_sheet)
from website.sync_xlsx import import_file_xlsx
from openpyxl import load_workbook
from django.core.files.storage import FileSystemStorage

def index(request):
    init_val = {}
    myteams = []
    free_athlets = 0
    if request.user.is_authenticated:
        init_val = {
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
            "phone": request.user.profile.phone,
        }
        myteams = Team.objects.filter(owner=request.user, year=2019)
        free_athlets = Athlet.objects.filter(
            owner=request.user, team=None).count()
    reg_form = RegForm(request.POST or None, initial=init_val)
    reg_form.set_user(request.user)

    if request.method == 'POST' and reg_form.is_valid():
        user = reg_form.reg_user()
        auth_login(request, user)
        return HttpResponseRedirect("/team")

    teams_count, members_count = Team().get_info()
    members_count = 670

    contex = {
        "cost": PaymentsYa().get_cost(),
        "reg_form": reg_form,
        "team_count": teams_count,
        "people_count": int(members_count),
        'myteams': myteams,
        'myteams_count': len(myteams),
        'free_athlet': free_athlets,
        'reg_open': settings.REG_OPEN,
    }
    return render(request, 'website/index.html', contex)


def index_dummy(request):
    return render(request, 'website/index_dummy.html')


def passlogin(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect("/")
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.authenticate_user()
        auth_login(request, user)
        return HttpResponseRedirect("/")

    return render(request, 'website/passlogin.html', {'form': form})


def login(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect("/")
    form = FastLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login = FastLogin()
        login_key = login.new_login_link(form.cleaned_data['email'])
        send_login_email(form.cleaned_data['email'], login_key)
        return render(request, 'website/login.html', {'success': 'ok',
                                                      'form': form})

    return render(request, 'website/login.html', {'form': form})


def login_by_key(request, login_key=""):
    keys = FastLogin.objects.filter(login_key=login_key)
    for key in keys:
        curr_time = datetime.now(timezone.utc)
        delta = curr_time - key.created_at
        if delta.seconds < 24*60*60:
            if request.method == 'POST' and 'login_key' in request.POST \
                    and request.POST['login_key'] == login_key:
                user = key.user
                auth_login(request, user)
                return HttpResponseRedirect("/")
            else:
                return render(request, 'website/login.html', {
                    'success': 'enter',
                    'login_key': login_key,
                    'username': key.user.first_name + ' ' + key.user.last_name,
                })
    return HttpResponseRedirect("/login")


def logout_user(request):
    if request.method == "POST":
        if "logout" in request.POST and request.POST["logout"] == "logout":
            if request.user.is_authenticated:
                logout(request)
                return HttpResponseRedirect("/")
    raise Http404("File not found.")


def teams(request, template=""):
    teams = [
        {
            'teams': Team.objects.filter(dist="6h", year='2019', category='').order_by('start_number'),
            'dist_name': '6ч'
        },
        {
            'teams': Team.objects.filter(dist="12h", year='2019', category='').order_by('start_number'),
            'dist_name': '12ч',
        },
        {
            'teams': Team.objects.filter(dist="24h", year='2019', category='').order_by('start_number'),
            'dist_name': '24ч',
        },
        {
            'teams': Team.objects.filter(category="24h", year='2019').order_by('start_number'),
            'dist_name': '"Точка невозврата" (24ч, 4-6 человек)',
        },
        {
            'teams': Team.objects.filter(category="12h_team", year='2019').order_by('start_number'),
            'dist_name': '"Только вперед" (12ч, 4-6 человек)',
        },
        {
            'teams': Team.objects.filter(category="12h_mm", year='2019').order_by('start_number'),
            'dist_name': '"Только вперед" (12ч, ММ)',
        },
        {
            'teams': Team.objects.filter(category="12h_mw", year='2019').order_by('start_number'),
            'dist_name': '"Только вперед" (12ч, МЖ)',
        },
        {
            'teams': Team.objects.filter(category="12h_ww", year='2019').order_by('start_number'),
            'dist_name': '"Только вперед" (12ч, ЖЖ)',
        },
        {
            'teams': Team.objects.filter(category="6h", year='2019').order_by('start_number'),
            'dist_name': '"Первые шаги" (6ч, 2-3 человека)'
        },
    ]

    # select only paid teams
    for t in teams:
        t['teams'] = [team for team in t['teams'] if team.paid_sum > 0]

    if request.user.is_superuser:
        teams.append({
            'teams': Team.objects.filter(paid_sum__lt=1, year=2019),
            'dist_name': 'Неоплаченное',
        })

    context = {
        'teams': teams,
    }
    return render(request, 'website/teams%s.html' % template, context)


def teams_predstart(request):
    return teams(request, template='_predstart')


def teams_start(request):
    teams = [
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category="6h", start_time__isnull=True, year=2019),
            'dist_name': 'Дистанция 6ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category__startswith="12h", start_time__isnull=True, year=2019),
            'dist_name': 'Дистанция 12ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category="24h", start_time__isnull=True, year=2019),
            'dist_name': 'Дистанция 24ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, start_time__isnull=False, year=2019).order_by('start_time'),
            'dist_name': 'Стартовавшие'
        },
    ]
    for teamgroup in teams:
        for team in teamgroup['teams']:
            if team.start_time:
                team.start_time = team.start_time + timedelta(hours=5)
            if team.finish_time:
                team.finish_time = team.finish_time + timedelta(hours=5)

    context = {
        'teams': teams,
    }
    return render(request, 'website/teams_start.html', context)


def teams_finish(request):
    teams = [
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category="6h", finish_time__isnull=True, year=2019).order_by('start_number'),
            'dist_name': 'Дистанция 6ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category__startswith="12h", finish_time__isnull=True, year=2019).order_by('start_number'),
            'dist_name': 'Дистанция 12ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category="24h", finish_time__isnull=True, year=2019).order_by('start_number'),
            'dist_name': 'Дистанция 24ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, finish_time__isnull=False, year=2019).order_by('finish_time'),
            'dist_name': 'Финишировавшие'
        },
    ]
    for teamgroup in teams:
        for team in teamgroup['teams']:
            if team.start_time:
                team.start_time = team.start_time + timedelta(hours=5)
            if team.finish_time:
                team.finish_time = team.finish_time + timedelta(hours=5)

    context = {
        'teams': teams,
    }
    return render(request, 'website/teams_finish.html', context)


def teams_protocol(request):
    teams = [
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category="6h", year=2019),
            'dist_name': 'Дистанция 6ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category__startswith="12h", year=2019),
            'dist_name': 'Дистанция 12ч'
        },
        {
            'teams': Team.objects.filter(paid_sum__gt=1, category="24h", year=2019),
            'dist_name': 'Дистанция 24ч'
        },
    ]
    for teamgroup in teams:
        for team in teamgroup['teams']:
            if team.start_time:
                team.start_time = team.start_time + timedelta(hours=5)

    context = {
        'teams': teams,
    }
    return render(request, 'website/teams_protocol.html', context)


def success(request, teamid=""):
    team = Team.objects.filter(paymentid=teamid, year=2019)[:1]
    if team:
        context = {
            "team": team[0],
        }
        return render(request, 'website/success.html', context)
    raise Http404("File not found.")


@login_required
def my_team(request, teamid="", template="my_team"):
    team_form = TeamForm(request.POST or None)
    paymentid = team_form.init_vals(request.user, teamid)
    if not paymentid:
        raise Http404("Nothing found")

    if request.user.is_superuser:
        team_form_admin = TeamFormAdmin(None)
        team_form_admin.init_vals(request.user, teamid)

    cost_now = PaymentsYa().get_cost()

    if request.method == 'GET':
        if teamid != paymentid:
            return HttpResponseRedirect("/team/%s" % paymentid)

        main_team = Team.objects.get(paymentid=paymentid, year=2019)
        other_teams = Team.objects.filter(owner=request.user, year=2019).exclude(
            paymentid=paymentid)
        if main_team.start_time:
            main_team.start_time += timedelta(hours=5)
        if main_team.finish_time:
            main_team.finish_time += timedelta(hours=5)
        context = {
            "cost": cost_now,
            "team_form": team_form,
            "other_teams": other_teams,
            "main_team": main_team,
            "curr_time": datetime.now(timezone.utc) + timedelta(hours=5),
            "timestamp": time(),
            'reg_open': settings.REG_OPEN,
        }
        if request.user.is_superuser:
            context['team_form_admin'] = team_form_admin
        return render(request, 'website/%s.html' % template, context)
    elif request.method == 'POST' and team_form.is_valid():
        if team_form.access_possible(request.user):
            team = team_form.save()
            if not team:
                Http404("Wrong values")
            response_data = {}
            response_data['sum'] = (team.ucount - team.paid_people) * cost_now

            new_event = PaymentLog(
                team=team,
                payment_method="save",
                paid_sum=response_data['sum']
            )
            new_event.save()
            response_data['success'] = 'true'
            return JsonResponse(response_data)
    raise Http404("Wrong values")


def team_predstart(request, teamid=""):
    if request.user.is_superuser:
        return my_team(request, teamid, "team_predstart")
    raise Http404("Not found")


def team_start(request, teamid=""):
    if request.user.is_superuser:
        return my_team(request, teamid, "team_start")
    raise Http404("Not found")


def team_finish(request, teamid=""):
    if request.user.is_superuser:
        return my_team(request, teamid, "team_finish")
    raise Http404("Not found")


@login_required
def team_admin(request, teamid=""):
    team_form_admin = TeamFormAdmin(request.POST or None)
    if request.method == 'POST' and team_form_admin.is_valid():
        if request.user.is_superuser:
            team = team_form_admin.save(request.user)
            if not team:
                Http404("Wrong values")
            response_data = {}
            response_data['success'] = 'true'
            return JsonResponse(response_data)
    raise Http404("Wrong values")


def new_payment(request):
    if request.method == "POST":
        paymentid = request.POST['paymentid'] if 'paymentid' in request.POST else ''
        team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
        if not team:
            raise Http404("Team not found")
        payment_method = request.POST['payment_method'] if 'payment_method' in request.POST else ''
        if not payment_method:
            payment_method = 'visamc'

        team = team.get()
        payment = Payment()
        if request.user.is_authenticated:
            payment.owner = request.user
        payment.team = team
        payment.payment_method = payment_method

        cost_now = PaymentsYa().get_cost()
        cost = (team.ucount - team.paid_people) * cost_now
        payment.payment_amount = cost
        payment.payment_with_discount = cost  # ! FIXME: need add coupon
        payment.cost_per_person = cost_now
        payment.paid_for = team.ucount - team.paid_people
        payment.status = 'draft'
        payment.save()
        pid = payment.id
        response_data = {}
        response_data['success'] = 'true'
        response_data['payment_id'] = str(pid)
        response_data['sum'] = cost
        response_data['paymentmethod'] = payment_method
        if payment_method == "visamc":
            response_data['yandexwallet'] = settings.YANDEX_WALLET
        if payment_method == "yandexmoney":
            response_data['yandexwallet'] = settings.YANDEX_WALLET
        if payment_method == "sberbank":
            response_data['cardnumber'] = settings.SBERBANK_INFO["cardnumber"]
            # response_data['cardholder_phone'] = settings.SBERBANK_INFO["phone"]
            response_data['cardholder_name'] = settings.SBERBANK_INFO["name"]
            response_data['today_date'] = strftime("%d.%m.%Y", gmtime())
        if payment_method == "tinkoff":
            response_data['paymentmethod'] = 'tinkoff'
            response_data['cardnumber'] = settings.TINKOFF_INFO["cardnumber"]
            response_data['cardholder_phone'] = settings.TINKOFF_INFO["phone"]
            response_data['cardholder_name'] = settings.TINKOFF_INFO["name"]
            response_data['today_date'] = strftime("%d.%m.%Y", gmtime())

        new_event = PaymentLog(
            team=team,
            payment_method=payment_method,
            paid_sum=response_data['sum']
        )
        new_event.save()

        return JsonResponse(response_data)
    raise Http404("Wrong values")


def paymentinfo(request):
    if request.method == "POST":
        new_payment_id = request.POST['paymentid']
        # print(request.POST)
        payment = Payment.objects.filter(id=new_payment_id)[:1]
        if payment:
            payment = payment.get()
            payment.status = 'draft_with_info'
            payment.sender_card_number = request.POST['sender_card_number'] +' ' + request.POST['payment_sum']
            pdate = datetime.strptime(request.POST['payment_date'], '%d.%m.%Y')
            payment.payment_date = pdate
            payment.save()
            response_data = {}
            response_data['paymentmethod'] = payment.payment_method
            response_data['success'] = 'true'
            return JsonResponse(response_data)
    raise Http404("Wrong values")

def get_cost(request):
    response_data = {}
    response_data['success'] = 'true'
    response_data["cost"] = PaymentsYa().get_cost()
    return JsonResponse(response_data)

@login_required
def new_team(request):
    if request.method == "POST":
        team = Team()
        team.new_team(request.user, '12h', 4)
        team.save()
    return HttpResponseRedirect("/team/%s" % team.paymentid)


@csrf_exempt
def yandex_payment(request):
    if request.method == 'POST':
        payment = PaymentsYa()
        if payment.new_payment(request.POST):
            # send_success_email(payment.label, payment.withdraw_amount, notification_type)
            return HttpResponse("Ok")
        else:
            raise Http404("Wrong values")
    raise Http404("File not found.")


def import_categories(request):
    if request.user.is_superuser:
        count = import_category_from_sheet()
        return HttpResponse("Updated: %s" % count)
    else:
        raise Http404("File not found.")


def export_payments(request):
    if request.user.is_superuser:
        count = export_payments_to_sheet()
        return HttpResponse("Updated: %s" % count)
    else:
        raise Http404("File not found.")


def sync_googledocs(request):
    if request.user.is_superuser:
        form = Export2GoogleDocsForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            if form.cleaned_data['sync_type'] == 'export_team':
                if export_teams(form.googlekey):
                    return render(
                        request,
                        'website/sync_googledocs.html',
                        {'success': 'export', 'form': form}
                    )
                return HttpResponse("Export failed")
            elif form.cleaned_data['sync_type'] == 'export_team_pretty':
                if export_teams_pretty(form.googlekey):
                    return render(
                        request,
                        'website/sync_googledocs.html',
                        {'success': 'export', 'form': form}
                    )
                return HttpResponse("Export failed")
            elif form.cleaned_data['sync_type'] == 'import_team_numbers':
                count = import_start_numbers_from_sheet(form.googlekey)
                return render(
                    request,
                    'website/sync_googledocs.html',
                    {'success': 'import', 'count': count, 'form': form}
                )
            else:
                raise Http404("File not found.")
        return render(request, 'website/sync_googledocs.html', {'form': form})
    else:
        raise Http404("File not found.")


def update_protocol(request):
    if not request.user.is_staff:
        raise Http404("File not found.")
    
    wb = load_workbook(filename = settings.PROTOCOL_DIR + 'protokol.xlsx')
    # grab the active worksheet
    sheet_tabs = {'6h': '6ч', '12h_ww': '12ч_ЖЖ', '12h_mw': '12ч_МЖ',
                  '12h_mm': '12ч_ММ', '12h_team': '12ч_группа', '24h': '24ч'}

    for tab in sheet_tabs:
        ws = wb[sheet_tabs[tab]]
        teams = Team.objects.filter(category=tab, year='2019').order_by('start_number')
        # select only paid teams
        teams = [team for team in teams if team.paid_sum > 0]
        line = 10
        for team in teams:
            row = str(line)
            ws['B'+row] = team.start_number
            ws['C'+row] = team.teamname+ ', '+ team.city
            athlets = [team.athlet1, team.athlet2, team.athlet3,
                    team.athlet4, team.athlet5, team.athlet6]
            athlets = ", ".join(athlets[:int(team.paid_people)])
            ws['D'+row] = athlets
            ws['E'+row] = team.start_time + timedelta(hours=5) if team.start_time else ''
            ws['F'+row] = team.finish_time + timedelta(hours=5) if team.finish_time else ''
            if team.start_time and team.finish_time:
                ws['G'+row] = team.finish_time - team.start_time
            line += 1

    # Save the file
    filename = "protokol2019.xlsx"
    wb.save(settings.PROTOCOL_DIR + filename)
    return render(request, 'website/save_protokol.html',
                  {'success': 'save', 'file_url': settings.PROTOCOL_URL + filename}
                  )


def upload_protocol(request):
    if not request.user.is_staff:
        raise Http404("File not found.")

    if request.method == 'POST' and request.FILES['myfile']:
        myfile = request.FILES['myfile']
        fs = FileSystemStorage()

        curr_time = datetime.now(timezone.utc) + timedelta(hours=5)
        file_prefix = 'uploads/' + str(curr_time.year) + \
            str(curr_time.month).zfill(2) + \
            str(curr_time.day).zfill(2) + "_" + \
            str(curr_time.hour).zfill(2) + \
            str(curr_time.minute).zfill(2) + \
            str(curr_time.second).zfill(2) + "_"

        filename = fs.save(settings.PROTOCOL_DIR +
                           file_prefix + myfile.name, myfile)

        uploaded_file_url = fs.url(
            settings.PROTOCOL_URL + file_prefix + myfile.name)

        #read this file:
        err, msg = import_file_xlsx(filename)

        return render(request, 'website/simple_upload.html', {
            'uploaded_file_url': uploaded_file_url, 'err': err, 'msg': msg
        })
    return render(request, 'website/simple_upload.html')
