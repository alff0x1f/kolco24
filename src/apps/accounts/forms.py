import random
import string

from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.utils.safestring import mark_safe

from website.models import Athlet, Team

DUPLICATE_EMAIL_MSG = mark_safe(
    "Пользователь с таким email уже зарегистрирован. "
    '<a href="/accounts/login/">Войдите</a> в существующий аккаунт.'
)


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Введите email",
            }
        ),
        label="Адрес email:",
    )

    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Введите пароль",
            }
        ),
        label="Пароль:",
    )


class CustomPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Введите email",
            }
        ),
        label="Адрес email:",
    )


class CustomSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="Новый пароль",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Введите новый пароль",
            }
        ),
    )

    new_password2 = forms.CharField(
        label="Подтвердите новый пароль",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Подтвердите новый пароль",
            }
        ),
    )


class RegForm(forms.Form):
    first_name = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Имя"})
    )
    last_name = forms.CharField(
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Фамилия"}
        )
    )
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "Введите email"}
        ),
        label="Адрес email",
    )
    phone = forms.CharField(
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Телефон"}
        ),
        label="Телефон",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Введите пароль"}
        ),
        label="Пароль",
    )
    ucount = forms.IntegerField(required=False)
    dist = forms.CharField(required=False)
    agree_privacy = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        require_agreements = kwargs.pop("require_agreements", True)
        super(RegForm, self).__init__(*args, **kwargs)
        if not require_agreements:
            self.fields["agree_privacy"].required = False

    @staticmethod
    def id_generator(
        size=8,
        chars=string.ascii_uppercase + string.digits + string.ascii_lowercase,
    ):
        return "".join(random.choice(chars) for _ in range(size))

    def reg_user(self, request_user: User):
        first_name = self.cleaned_data["first_name"]
        last_name = self.cleaned_data["last_name"]
        phone = self.cleaned_data["phone"]
        dist = self.cleaned_data["dist"]
        ucount = self.cleaned_data["ucount"]
        username = "%s %s" % (last_name, first_name)

        if request_user.is_anonymous:
            password = self.id_generator()
            email = self.cleaned_data["email"]
            while User.objects.filter(username=username).exists():
                username = self.id_generator(12)

            user = User.objects.create_user(username, email, password)
            user.first_name = first_name
            user.last_name = last_name
            user.profile.phone = phone
            user.save()

            if ucount == 1:
                athlet = Athlet()
                athlet.new_athlet(user, None, last_name + " " + first_name)
            elif ucount > 1:
                team = Team()
                team.new_team(user, dist, ucount)
            return user
        else:
            request_user.first_name = first_name
            request_user.last_name = last_name
            request_user.profile.phone = phone
            request_user.save()
            team = Team.objects.filter(owner=request_user, year=2023)[:1]
            if not team and ucount > 1:
                team = Team()
                team.new_team(request_user, dist, ucount)
        return request_user

    def clean(self):
        if "email" not in self.cleaned_data:
            return super(RegForm, self).clean()

        qs = User.objects.filter(email__iexact=self.cleaned_data["email"])
        if self.user and self.user.is_authenticated:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError(DUPLICATE_EMAIL_MSG)
        return super(RegForm, self).clean()


class ImpersonateForm(forms.Form):
    query = forms.CharField(
        label="Email или ID пользователя",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Введите email или ID пользователя",
                "autocomplete": "off",
            }
        ),
    )
    next = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_query(self):
        return self.cleaned_data["query"].strip()
