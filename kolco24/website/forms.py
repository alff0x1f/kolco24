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
            # self.user.username = username
            self.user.save()
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
    ucount = 2

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
