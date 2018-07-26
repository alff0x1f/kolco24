from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from website.forms import LoginForm, RegForm, TeamForm
from website.models import Payments, Team
from django.http import HttpResponseRedirect, Http404, JsonResponse


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
        "cost": 500,
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

@login_required
def my_team(request, teamid=""):
    team_form = TeamForm(request.POST or None)
    paymentid = team_form.init_vals(request.user, teamid)
    if not paymentid:
        raise Http404("Nothing found")

    if request.method == 'GET':
        if teamid != paymentid:
            return HttpResponseRedirect("/team/%s" % paymentid)

        other_teams = Team.objects.filter(owner=request.user).exclude(
            paymentid=paymentid)
        context = {
            "cost": 500,
            "team_form": team_form,
            "other_teams": other_teams,
        }
        return render(request, 'website/my_team.html', context)
    elif request.method == 'POST' and team_form.is_valid():
        # print(team_form.fields)
        if team_form.access_possible(request.user):
            team_form.save()
            response_data = {}
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
