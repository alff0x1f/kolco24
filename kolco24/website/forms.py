import random
import string
from datetime import timedelta
from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from website.models import Team, Athlet, TeamAdminLog


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
            
            old_user = User.objects.filter(email__iexact=self.cleaned_data["email"])[:1]
            if old_user:
                user = old_user.get()
            else:
                user = User.objects.create_user(username, email, password)
            user.first_name = first_name
            user.last_name = last_name
            user.profile.phone = phone
            user.save()

            if ucount == 1:
                athlet = Athlet()
                athlet.new_athlet(user, None, last_name + ' ' + first_name)
            elif ucount > 1:
                team = Team()
                team.new_team(user, dist, ucount)
            return user
        else:
            self.user.first_name = first_name
            self.user.last_name = last_name
            self.user.profile.phone = phone
            self.user.save()
            team = Team.objects.filter(owner=self.user, year=2019)[:1]
            if not team and ucount > 1:
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

        exist_user = User.objects.filter(email__iexact=self.cleaned_data["email"])[:1]
        if exist_user:
            team = Team.objects.filter(owner=exist_user, year=2019)
            u_email = "@@@" if self.user.is_anonymous else self.user.email.lower()
            if not team:
                u_email = self.cleaned_data["email"].lower()
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
            team = Team.objects.filter(owner=user, paymentid=paymentid, year=2019)[:1]
            if not team and user.is_superuser:
                team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
            if not team:
                return False
        else:
            team = Team.objects.filter(owner=user, year=2019)[:1]
        if not team:
            # free_athlet = Athlet.objects.filter(owner=user, team=None)[:1]
            # if free_athlet:
            #     return False
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
        team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
        if team and team.get().owner == user:
            return True

    def clean(self):
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
        if not team:
            raise forms.ValidationError("Команда не найдена.")
        return self.cleaned_data

    def save(self):
        if "paymentid" not in self.cleaned_data:
            return False
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
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


class TeamFormAdmin(forms.Form):
    paymentid = forms.CharField(widget=forms.HiddenInput())
    get_package = forms.BooleanField(
        required=False,
        label = 'Получили стартовый пакет',
        widget=forms.CheckboxInput(
            attrs = {
                'class': 'custom-control-input',
            })
    )
    get_number = forms.BooleanField(
        required=False,
        label = 'Получили номер',
        widget=forms.CheckboxInput(
            attrs = {
                'class': 'custom-control-input',
            })
        )
    get_map = forms.BooleanField(
        required=False,
        label = 'Получили карту',
        widget=forms.CheckboxInput(
            attrs = {
                'class': 'custom-control-input',
            })
        )
    give_paper = forms.BooleanField(
        required=False,
        label = 'Сдали заявку',
        widget=forms.CheckboxInput(
            attrs = {
                'class': 'custom-control-input',
            })
        )
    give_photos = forms.BooleanField(
        required=False,
        label = 'Сдали фото',
        widget=forms.CheckboxInput(
            attrs = {
                'class': 'custom-control-input',
            })
        )
    dnf = forms.BooleanField(
        required=False,
        label = 'Не финишировали',
        widget=forms.CheckboxInput(
            attrs = {
                'class': 'custom-control-input',
            })
    )

    category = forms.ChoiceField(
        required = False,
        choices = (
            ('6h', '6 часов'),
            ('12h_mw','12 часов МЖ'),
            ('12h_mm', '12 часов ММ'),
            ('12h_ww', '12 часов ЖЖ'),
            ('12h_team', '12 часов, команда'), 
            ('24h', '24 часа'),
        ),
        label = 'Категория',
        widget = forms.Select(
            attrs = {
                'class': 'form-control form-control-lg',
                'placeholder': 'Категория'})
    )
    start_number = forms.CharField(
        required = False,
        label = 'Стартовый номер',
        widget = forms.TextInput(
            attrs = {
                'class': 'form-control form-control-lg',
                'placeholder': 'Стартовый номер'})
    )
    start_time = forms.DateTimeField(
        required = False,
        label = 'Время старта',
        widget = forms.DateTimeInput(
            attrs = {
                'class': 'form-control form-control-lg',
                'placeholder': 'Время старта'})
    )
    finish_time = forms.DateTimeField(
        required = False,
        label = 'Время финиша',
        widget = forms.DateTimeInput(
            attrs = {
                'class': 'form-control form-control-lg',
                'placeholder': 'Время финиша'})
    )
    penalty = forms.IntegerField(
        required = False,
        min_value = 0,
        max_value = 1000,
        label = 'Штраф',
        widget=forms.NumberInput(
            attrs = {
                'class': 'form-control form-control-lg',
                'placeholder': 'Штраф'})
    )

    def clean(self):
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
        if not team:
            raise forms.ValidationError("Команда не найдена.")
        return self.cleaned_data
    
    def init_vals(self, user, paymentid=""):
        team = None
        if paymentid and user.is_superuser:
            team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
            if not team:
                return False
        else:
            return False

        team = team.get()
        self.initial["get_package"] = team.get_package
        self.initial["get_number"] = team.get_number
        self.initial["get_map"] = team.get_map
        self.initial["give_paper"] = team.give_paper
        self.initial["give_photos"] = team.give_photos
        self.initial["category"] = team.category
        self.initial["start_number"] = team.start_number
        self.initial["start_time"] = team.start_time + timedelta(hours=5) if team.start_time else None
        self.initial["finish_time"] = team.finish_time + timedelta(hours=5) if team.finish_time else None
        self.initial["penalty"] = team.penalty
        self.initial["dnf"] = team.dnf
    
    def save(self, user):
        if "paymentid" not in self.cleaned_data:
            return False
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2019)[:1]
        if team:
            d = self.cleaned_data
            print(d)
            team = team.get()
            if "get_package" in d :
                team.get_package = d["get_package"]
            if "get_number" in d:
                team.get_number = d["get_number"]
            if "get_map" in d:
                team.get_map = d["get_map"]
            if "give_paper" in d:
                team.give_paper = d["give_paper"]
            if "give_photos" in d:
                team.give_photos = d["give_photos"]
            if "category" in d:
                team.category = d["category"]
            if "start_number" in d:
                team.start_number = d["start_number"]
            if "start_time" in d and d["start_time"]:
                team.start_time = d["start_time"] - timedelta(hours=5)
            if "finish_time" in d and d["finish_time"]:
                team.finish_time = d["finish_time"] - timedelta(hours=5)
            if "penalty" in d:
                team.penalty = d["penalty"] if d["penalty"] else 0
            if "dnf" in d:
                team.dnf = d["dnf"]
            team.save()

            team_admin_log = TeamAdminLog()
            if user:
                team_admin_log.editor = user
            team_admin_log.paymentid     = team.paymentid
            team_admin_log.get_package   = team.get_package
            team_admin_log.get_number    = team.get_number
            team_admin_log.get_map    = team.get_map
            team_admin_log.give_paper    = team.give_paper
            team_admin_log.give_photos   = team.give_photos
            team_admin_log.category      = team.category
            team_admin_log.start_number  = team.start_number
            team_admin_log.start_time    = team.start_time
            team_admin_log.finish_time   = team.finish_time
            team_admin_log.distance_time = team.distance_time
            team_admin_log.penalty       = team.penalty
            team_admin_log.dnf           = team.dnf
            team_admin_log.save()
            return team
        return False


class Export2GoogleDocsForm(forms.Form):
    urladdress = forms.CharField(
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'https://docs.google.com/spreadsheets/d/...'}),
        label='Введите адрес Таблицы Google Docs')
    sync_type = forms.ChoiceField(
        choices = (
            ('export_team', 'Экпортировать команды'),
            ('export_team_pretty', 'Экпортировать команды (для печати)'),
            ('import_team_numbers','Импорт номеров команд'),
        ),
        label = 'Синхронизировать',
        widget = forms.Select(
            attrs = {
                'class': 'form-control form-control-lg',
                'placeholder': 'Синхронизация'})
    )

    def clean(self):
        urladdress = self.cleaned_data['urladdress']
        googlekey = self.extractKeyFromUrl(urladdress)
        if not googlekey:
            raise forms.ValidationError("Необходимо ввести корректный адрес для экспорта")
        else:
            self.googlekey = googlekey
        return self.cleaned_data

    def extractKeyFromUrl(self, url):
        if len(url) <= 40:
            return False
        if url[:39] == 'https://docs.google.com/spreadsheets/d/':
            url = url[39:]
        return url.split("/")[0]
