from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from website.forms import LoginForm, RegForm, TeamForm
from website.models import Payments, Team, PaymentLog
from django.http import HttpResponseRedirect, Http404, JsonResponse
from django.conf import settings


def index(request):
    init_val = {}
    if request.user.is_authenticated:
        init_val = {
            "first_name":request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
            "phone": request.user.profile.phone,
            }
    reg_form = RegForm(request.POST or None, initial=init_val)
    reg_form.set_user(request.user)

    if request.method == 'POST' and reg_form.is_valid():
        user = reg_form.reg_user()
        auth_login(request, user)
        return HttpResponseRedirect("/team")
    contex = {
        "cost": Payments().get_cost(),
        "reg_form": reg_form
    }
    return render(request, 'website/index.html', contex)

def index_dummy(request):
    return render(request, 'website/index_dummy.html')

def login(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect("/")
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.authenticate_user()
        auth_login(request, user)
        return HttpResponseRedirect("/")

    return render(request, 'website/login.html', {'form': form})

def logout_user(request):
    if request.method == "POST":
        if "logout" in request.POST and request.POST["logout"] == "logout":
            if request.user.is_authenticated:
                logout(request)
                return HttpResponseRedirect("/")
    raise Http404("File not found.")

def teams(request):
    teams_6h = Team.objects.filter(dist="6h")
    teams_12h_2 = Team.objects.filter(dist="12h", ucount=2)
    teams_12h_4 = Team.objects.filter(dist="12h", ucount__gt=2)
    teams_24h = Team.objects.filter(dist="24h")

    # select only paid teams
    if not request.user.is_superuser:
        teams_6h = [team for team in teams_6h if team.paid_sum > 0]
        teams_12h_2 = [team for team in teams_12h_2 if team.paid_sum > 0]
        teams_12h_4 = [team for team in teams_12h_4 if team.paid_sum > 0]
        teams_24h = [team for team in teams_24h if team.paid_sum > 0]
    else:
        teams_12h_2 = [team for team in teams_12h_2]
        teams_12h_4 = [team for team in teams_12h_4]
    teams_12h = teams_12h_2 + teams_12h_4
        

    context = {
        "teams_6h":teams_6h,
        "teams_12h":teams_12h,
        "teams_24h":teams_24h,
    }
    return render(request, 'website/teams.html', context)

@login_required
def my_team(request, teamid=""):
    team_form = TeamForm(request.POST or None)
    paymentid = team_form.init_vals(request.user, teamid)
    if not paymentid:
        raise Http404("Nothing found")

    cost_now = Payments().get_cost()

    if request.method == 'GET':
        if teamid != paymentid:
            return HttpResponseRedirect("/team/%s" % paymentid)

        main_team = Team.objects.get(paymentid=paymentid)
        other_teams = Team.objects.filter(owner=request.user).exclude(
            paymentid=paymentid)
        context = {
            "cost": cost_now,
            "team_form": team_form,
            "other_teams": other_teams,
            "main_team": main_team,
        }
        return render(request, 'website/my_team.html', context)
    elif request.method == 'POST' and team_form.is_valid():
        if team_form.access_possible(request.user):
            team = team_form.save()
            if not team:
                Http404("Wrong values")
            response_data = {}
            response_data['paymentmethod'] = ""
            paymentmethod = request.POST["paymentmethod"] if \
                "paymentmethod" in request.POST else ""
            response_data['sum'] = (team.ucount - team.paid_people) * cost_now

            new_event = PaymentLog(
                team=team,
                payment_method=paymentmethod, 
                paid_sum=response_data['sum']
            )
            new_event.save()

            if paymentmethod == "visamc":
                response_data['paymentmethod'] = 'visamc'
                response_data['paymentmetid'] = team.paymentid
                response_data['yandexwallet'] = settings.YANDEX_WALLET
            if paymentmethod == "yandexmoney":
                response_data['paymentmethod'] = 'yandexmoney'
                response_data['paymentmetid'] = team.paymentid
                response_data['yandexwallet'] = settings.YANDEX_WALLET
            if paymentmethod == "sberbank":
                response_data['paymentmethod'] = 'sberbank'
                response_data['cardnumber'] = settings.SBERBANK_INFO["cardnumber"]
                response_data['cardholder_phone'] = settings.SBERBANK_INFO["phone"]
                response_data['cardholder_name'] = settings.SBERBANK_INFO["name"]
                response_data['payment_comment'] = "команда%s" % team.id
            if paymentmethod == "tinkoff":
                response_data['paymentmethod'] = 'tinkoff'
                response_data['cardnumber'] = settings.TINKOFF_INFO["cardnumber"]
                response_data['cardholder_phone'] = settings.TINKOFF_INFO["phone"]
                response_data['cardholder_name'] = settings.TINKOFF_INFO["name"]
                response_data['payment_comment'] = "команда%s" % team.id
            response_data['success'] = 'true'
            return JsonResponse(response_data)
    raise Http404("Wrong values")

@login_required
def new_team(request):
    if request.method == "POST":
        team = Team()
        team.new_team(request.user, '12h', 4)
        team.save()
    return HttpResponseRedirect("/team/%s"%team.paymentid)

@csrf_exempt
def yandex_payment(request):
    if request.method=='POST':
        payment = Payments()
        if payment.new_payment(request.POST):
            # send_success_email(payment.label, payment.withdraw_amount, notification_type)
            return HttpResponse("Ok")
        else:
            raise Http404("Wrong values")
    raise Http404("File not found.")
