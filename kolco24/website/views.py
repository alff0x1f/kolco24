from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth import login as auth_login
from website.forms import LoginForm
from django.http import HttpResponseRedirect


def index(request):
    # return HttpResponse("Hello, world!")
    return render(request, 'website/index.html')


def login(request):
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.authenticate_user()
        auth_login(request, user)
        return HttpResponseRedirect("/")

    return render(request, 'website/login.html', {'form': form})
