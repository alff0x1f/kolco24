import random
import string
from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from website.models import Team


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Введите email'}),
        label='Адрес email:')

    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Введите пароль'}),
        label='Пароль:')

    def clean(self):
        user = self.authenticate_via_email()
        if not user:
            raise forms.ValidationError("Неправильный логин или пароль.")
        else:
            self.user = user
        return self.cleaned_data

    def authenticate_user(self):
        return authenticate(
            username=self.user.username,
            password=self.cleaned_data['password'])

    def authenticate_via_email(self):
        """
            Authenticate user using email.
            Returns user object if authenticated else None
        """
        email = self.cleaned_data['email']
        if email:
            try:
                user = User.objects.get(email__iexact=email)
                if user.check_password(self.cleaned_data['password']):
                    return user
            except ObjectDoesNotExist:
                pass
        return None

class FastLoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Введите email'}),
        label='Адрес email:')

    def clean(self):
        email = self.cleaned_data['email']
        user = User.objects.filter(email__iexact=email)
        if not user:
            raise forms.ValidationError("Такой email не найден.")
        else:
            self.user = user
        return self.cleaned_data

class RegForm(forms.Form):
    first_name = forms.CharField(
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Имя'}))
    last_name = forms.CharField(
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Фамилия'}))
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Введите email'}),
        label='Адрес email')
    phone = forms.CharField(
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Телефон'}),
        label='Телефон')
    ucount = forms.IntegerField()
    dist = forms.CharField()

    def id_generator(self, size=8, chars=string.ascii_uppercase + string.digits \
            + string.ascii_lowercase):
        return ''.join(random.choice(chars) for _ in range(size))

    def set_user(self, user):
        self.user = user

    def reg_user(self):
        first_name = self.cleaned_data["first_name"]
        last_name = self.cleaned_data["last_name"]
        phone = self.cleaned_data["phone"]
        dist = self.cleaned_data["dist"]
        ucount = self.cleaned_data["ucount"]
        username = "%s %s" % (last_name, first_name)

        if self.user.is_anonymous:
            password = self.id_generator()
            email = self.cleaned_data["email"]
            while User.objects.filter(username=username).exists():
                username = self.id_generator(12)
            user = User.objects.create_user(username, email, password)
            user.first_name = first_name
            user.last_name = last_name
            user.profile.phone = phone
            user.save()

            team = Team()
            team.new_team(user, dist, ucount)
            return user
        else:
            self.user.first_name = first_name
            self.user.last_name = last_name
            self.user.profile.phone = phone
            self.user.save()
            team = Team.objects.filter(owner=self.user)[:1]
            if not team:
                team = Team()
                team.new_team(self.user, dist, ucount)
        return self.user

    def clean(self):
        # make invalid forms red:
        if self.errors:
            for f_name in self.fields:
                if f_name in self.errors:
                    classes = self.fields[f_name].widget.attrs.get('class', '')
                    classes += ' is-invalid'
                    self.fields[f_name].widget.attrs['class'] = classes
            raise forms.ValidationError("Заполните все поля")

        if User.objects.filter(email__iexact=self.cleaned_data["email"]).exists():
            u_email = "@@@" if self.user.is_anonymous else self.user.email.lower()
            if self.cleaned_data["email"].lower() != u_email:
                raise forms.ValidationError("Такой email уже зарегистрирован.")


class TeamForm(forms.Form):
    name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Название команды'})
    )
    city = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Город'})
    )
    organization = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'Клуб(организация)'})
    )
    ucount = forms.IntegerField()
    dist = forms.CharField()
    paymentid = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super(TeamForm, self).__init__(*args, **kwargs)
        for i in range(6):
            self.fields['athlet%s' % (i + 1)] = forms.CharField(
                required=False,
                widget=forms.TextInput(
                    attrs={
                        'class': 'form-control form-control-lg',
                        'placeholder': str(i + 1) + ') Фамилия имя'})
            )
            self.fields['birth%s' % (i+1)] = forms.CharField(
                required=False,
                widget=forms.TextInput(
                    attrs={
                        'class': 'form-control form-control-lg',
                        'placeholder': 'Год рождения'})
            )
    
    def init_vals(self, user, paymentid=""):
        team = None
        if paymentid:
            team = Team.objects.filter(owner=user, paymentid=paymentid)[:1]
            if not team and user.is_superuser:
                team = Team.objects.filter(paymentid=paymentid)[:1]
            if not team:
                return False
        else:
            team = Team.objects.filter(owner=user)[:1]
        if not team:
            team = Team()
            team.new_team(user, '12h', 4)
        else:
            team = team.get()

        self.initial["name"] = team.teamname
        self.initial["city"] = team.city
        self.initial["organization"] = team.organization
        self.initial["athlet1"] = team.athlet1
        self.initial["athlet2"] = team.athlet2
        self.initial["athlet3"] = team.athlet3
        self.initial["athlet4"] = team.athlet4
        self.initial["athlet5"] = team.athlet5
        self.initial["athlet6"] = team.athlet6
        self.initial["birth1"] = team.birth1 if team.birth1 else ""
        self.initial["birth2"] = team.birth2 if team.birth2 else ""
        self.initial["birth3"] = team.birth3 if team.birth3 else ""
        self.initial["birth4"] = team.birth4 if team.birth4 else ""
        self.initial["birth5"] = team.birth5 if team.birth5 else ""
        self.initial["birth6"] = team.birth6 if team.birth6 else ""
        self.initial["dist"] = team.dist
        self.initial["ucount"] = team.ucount
        self.initial["paymentid"] = team.paymentid

        return team.paymentid

    def access_possible(self, user):
        if "paymentid" not in self.cleaned_data:
            return False
        paymentid = self.cleaned_data["paymentid"]
        if user.is_superuser:
            return True
        team = Team.objects.filter(paymentid=paymentid)[:1]
        if team and team.get().owner == user:
            return True

    def clean(self):
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid)[:1]
        if not team:
            raise forms.ValidationError("Команда не найдена.")
        return self.cleaned_data

    def save(self):
        if "paymentid" not in self.cleaned_data:
            return False
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid)[:1]
        if team:
            d = self.cleaned_data
            print(d)
            team = team.get()
            team.dist = d["dist"] if "dist" in d else "12h"
            team.ucount = d["ucount"] if "ucount" in d else 2
            team.teamname = d["name"] if "name" in d else ""
            team.city = d["city"] if "city" in d else ""
            team.organization = d["organization"] if "organization" in d else ""
            team.athlet1 = d["athlet1"] if "athlet1" in d else ""
            team.athlet2 = d["athlet2"] if "athlet2" in d else ""
            team.athlet3 = d["athlet3"] if "athlet3" in d else ""
            team.athlet4 = d["athlet4"] if "athlet4" in d else ""
            team.athlet5 = d["athlet5"] if "athlet5" in d else ""
            team.athlet6 = d["athlet6"] if "athlet6" in d else ""
            team.birth1 = d["birth1"] if d["birth1"].isdigit() else "0"
            team.birth2 = d["birth2"] if d["birth2"].isdigit() else "0"
            team.birth3 = d["birth3"] if d["birth3"].isdigit() else "0"
            team.birth4 = d["birth4"] if d["birth4"].isdigit() else "0"
            team.birth5 = d["birth5"] if d["birth5"].isdigit() else "0"
            team.birth6 = d["birth6"] if d["birth6"].isdigit() else "0"
            team.save()
            return team
        return False
