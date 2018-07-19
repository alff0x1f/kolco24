from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from website.forms import LoginForm
from django.http import HttpResponseRedirect, Http404


def index(request):
    contex = {
        "cost": 500
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
                return render(request, 'website/index.html')
    raise Http404("File not found.")
