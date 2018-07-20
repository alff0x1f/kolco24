from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from website.forms import LoginForm, RegForm
from django.http import HttpResponseRedirect, Http404


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
        return HttpResponseRedirect("/")
    contex = {
        "cost": 500,
        "reg_form": reg_form
    }
    return render(request, 'website/index.html', contex)

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
