import random
import string
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.utils.safestring import mark_safe

from website.models import (
    Athlet,
    BreakfastRegistration,
    Team,
    TeamAdminLog,
    TeamMemberMove,
    Transfer,
)
from website.models.news import NewsPost, Page
from website.models.race import Category, Race

BUS_REGISTRATION_MAX_PASSENGERS = 20
BREAKFAST_MAX_ATTENDEES = 20
# Maps pricing — mirrored in the views and the JS config island.
FREE_MAPS = 2
MAP_PRICE = 200
DUPLICATE_EMAIL_MSG = mark_safe(
    "Пользователь с таким email уже зарегистрирован. "
    '<a href="/login/">Войдите</a> в существующий аккаунт.'
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


class TransferForm(forms.ModelForm):
    MAX_PASSENGERS = BUS_REGISTRATION_MAX_PASSENGERS

    class Meta:
        model = Transfer
        fields = ("people_count",)
        widgets = {
            "people_count": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                    "max": BUS_REGISTRATION_MAX_PASSENGERS,
                    "required": True,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passenger_field_pairs = []
        self._build_passenger_fields(self._initial_people_count())

    def _initial_people_count(self) -> int:
        if self.data:
            raw_value = self.data.get(self.add_prefix("people_count"))
        elif self.initial.get("people_count"):
            raw_value = self.initial.get("people_count")
        elif self.instance and self.instance.pk:
            raw_value = self.instance.people_count
        else:
            raw_value = 1

        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            count = 1

        return max(1, min(count, self.MAX_PASSENGERS))

    def _build_passenger_fields(self, count: int) -> None:
        existing_contacts = []
        if self.instance and self.instance.pk:
            existing_contacts = self.instance.passenger_contacts
        elif self.initial.get("passenger_contacts"):
            existing_contacts = self.initial.get("passenger_contacts")

        for index in range(1, count + 1):
            name_field = f"passenger_{index}_name"
            phone_field = f"passenger_{index}_phone"

            name_initial = None
            phone_initial = None
            if isinstance(existing_contacts, list) and len(existing_contacts) >= index:
                contact = existing_contacts[index - 1] or {}
                name_initial = contact.get("name")
                phone_initial = contact.get("phone")

            self.fields[name_field] = forms.CharField(
                label=f"Имя участника {index}",
                max_length=255,
                initial=name_initial,
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control",
                        "placeholder": f"Имя участника {index}",
                        "required": True,
                    }
                ),
            )
            self.fields[phone_field] = forms.CharField(
                label=f"Телефон участника {index}",
                max_length=64,
                initial=phone_initial,
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control",
                        "placeholder": "+7 (999) 123-45-67",
                        "required": True,
                    }
                ),
            )

            name_bound = self[name_field]
            phone_bound = self[phone_field]
            self.passenger_field_pairs.append(
                {
                    "index": index,
                    "name": name_bound,
                    "phone": phone_bound,
                }
            )

    def clean_people_count(self):
        count = self.cleaned_data.get("people_count")
        if not count:
            return 1
        if count < 1:
            raise forms.ValidationError("Количество должно быть не меньше 1")
        if count > self.MAX_PASSENGERS:
            raise forms.ValidationError(
                f"Мы можем обработать до {self.MAX_PASSENGERS} участников за одну "
                f"заявку"
            )
        return count

    def clean(self):
        cleaned_data = super().clean()
        people_count = cleaned_data.get("people_count") or 1
        passengers = []
        for index in range(1, people_count + 1):
            name_key = f"passenger_{index}_name"
            phone_key = f"passenger_{index}_phone"
            name = cleaned_data.get(name_key)
            phone = cleaned_data.get(phone_key)

            if not name:
                self.add_error(name_key, "Укажите имя участника")
            if not phone:
                self.add_error(phone_key, "Укажите телефон участника")

            if name and phone:
                passengers.append({"name": name, "phone": phone})

        cleaned_data["passenger_contacts"] = passengers
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.passenger_contacts = self.cleaned_data.get("passenger_contacts", [])
        instance.people_count = len(instance.passenger_contacts)
        if commit:
            instance.save()
        return instance


class BreakfastForm(forms.ModelForm):
    MAX_ATTENDEES = BREAKFAST_MAX_ATTENDEES

    class Meta:
        model = BreakfastRegistration
        fields = ("people_count",)
        widgets = {
            "people_count": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                    "max": BREAKFAST_MAX_ATTENDEES,
                    "required": True,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        self.race = kwargs.pop("race", None)
        super().__init__(*args, **kwargs)
        self.attendee_field_sets = []
        self._build_attendee_fields(self._initial_people_count())

    def _initial_people_count(self) -> int:
        if self.data:
            raw_value = self.data.get(self.add_prefix("people_count"))
        elif self.initial.get("people_count"):
            raw_value = self.initial.get("people_count")
        elif self.instance and self.instance.pk:
            raw_value = self.instance.people_count
        else:
            raw_value = 1

        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            count = 1

        return max(1, min(count, self.MAX_ATTENDEES))

    def _build_attendee_fields(self, count: int) -> None:
        existing_attendees = []
        if self.instance and self.instance.pk:
            existing_attendees = self.instance.attendees
        elif self.initial.get("attendees"):
            existing_attendees = self.initial.get("attendees")

        for index in range(1, count + 1):
            name_field = f"attendee_{index}_name"
            vegan_field = f"attendee_{index}_is_vegan"

            attendee_initial = {}
            if (
                isinstance(existing_attendees, list)
                and len(existing_attendees) >= index
            ):
                attendee_initial = existing_attendees[index - 1] or {}

            self.fields[name_field] = forms.CharField(
                label=f"Фамилия и имя {index}",
                max_length=255,
                initial=attendee_initial.get("name"),
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control",
                        "placeholder": f"Фамилия и имя {index}",
                        "required": True,
                    }
                ),
            )
            self.fields[vegan_field] = forms.BooleanField(
                label="Веганский вариант",
                required=False,
                initial=bool(attendee_initial.get("is_vegan")),
                widget=forms.CheckboxInput(
                    attrs={
                        "class": "form-check-input",
                    }
                ),
            )

            name_bound = self[name_field]
            vegan_bound = self[vegan_field]
            self.attendee_field_sets.append(
                {
                    "index": index,
                    "name": name_bound,
                    "is_vegan": vegan_bound,
                }
            )

    def clean_people_count(self):
        count = self.cleaned_data.get("people_count")
        if not count:
            return 1
        if count < 1:
            raise forms.ValidationError("Количество должно быть не меньше 1")
        if count > self.MAX_ATTENDEES:
            raise forms.ValidationError(
                f"Мы можем записать до {self.MAX_ATTENDEES} участников за одну заявку"
            )
        return count

    def clean(self):
        cleaned_data = super().clean()
        people_count = cleaned_data.get("people_count") or 1
        attendees = []
        for index in range(1, people_count + 1):
            name_key = f"attendee_{index}_name"
            vegan_key = f"attendee_{index}_is_vegan"
            name = cleaned_data.get(name_key)
            is_vegan = cleaned_data.get(vegan_key, False)

            if not name:
                self.add_error(name_key, "Укажите имя участника")

            if name:
                attendees.append(
                    {
                        "name": name,
                        "is_vegan": bool(is_vegan),
                    }
                )

        cleaned_data["attendees"] = attendees
        return cleaned_data

    def save(self, race=None, commit=True):
        instance = super().save(commit=False)
        instance.race = race or self.race
        instance.attendees = self.cleaned_data.get("attendees", [])
        instance.people_count = len(instance.attendees)
        if commit:
            instance.save()
        return instance


def category2_from_dist(dist, ucount):
    race = Race.objects.filter(code="kolco24_2023").first()
    category = Category.objects.filter(race=race)
    if dist == "12h":
        if ucount == 2:
            return category.filter(code="12h").first()
        return category.filter(code="12h_team").first()
    return category.filter(code=dist).first()


class TeamForm(forms.Form):
    teamname = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Название команды",
            }
        ),
    )
    city = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-lg", "placeholder": "Город"}
        ),
    )
    organization = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Клуб(организация)",
            }
        ),
    )
    ucount = forms.IntegerField(
        min_value=2,
        widget=forms.HiddenInput(),
        label="Количество участников",
    )
    dist = forms.CharField(required=False)
    paymentid = forms.CharField(widget=forms.HiddenInput(), required=False)

    map_count = forms.IntegerField(
        min_value=0,
        required=False,
        initial=0,
        widget=forms.HiddenInput(),
        label="Дополнительные карты",
    )

    def __init__(
        self,
        race_id,
        *args,
        current_category_id=None,
        team=None,
        bypass_limits=False,
        **kwargs,
    ):
        super(TeamForm, self).__init__(*args, **kwargs)
        # team = редактируемая команда (для само-исключения и расчёта роста);
        # на add передаётся Team() (paid_people=0, category2_id=None).
        self.team = team if team is not None else Team()
        self.bypass_limits = bypass_limits
        cats = list(Category.objects.filter(race_id=race_id, is_active=True))
        if current_category_id is not None:
            active_ids = {c.id for c in cats}
            if current_category_id not in active_ids:
                current_cat = Category.objects.filter(
                    id=current_category_id, race_id=race_id
                ).first()
                if current_cat:
                    cats.insert(0, current_cat)
        categories = (
            (category.id, f"{category.short_name} ({category.name})")
            for category in cats
        )
        self.fields["category2_id"] = forms.ChoiceField(
            required=False,
            choices=categories,
            label="Категория",
            widget=forms.Select(
                attrs={
                    "class": "form-control form-control-lg",
                    "placeholder": "Категория",
                }
            ),
        )
        for i in range(6):
            self.fields["athlet%s" % (i + 1)] = forms.CharField(
                required=False,
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control form-control-lg",
                        "placeholder": str(i + 1) + ") Фамилия имя",
                    }
                ),
            )
            self.fields["birth%s" % (i + 1)] = forms.CharField(
                required=False,
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control form-control-lg",
                        "placeholder": "Год рождения",
                    }
                ),
            )

    def init_vals(self, user, paymentid=""):
        team = None
        if paymentid:
            team = Team.objects.filter(owner=user, paymentid=paymentid, year=2024)[:1]
            if not team and user.is_superuser:
                team = Team.objects.filter(paymentid=paymentid, year=2024)[:1]
            if not team:
                return False
        else:
            team = Team.objects.filter(owner=user, year=2024)[:1]
        if not team:
            # free_athlet = Athlet.objects.filter(owner=user, team=None)[:1]
            # if free_athlet:
            #     return False
            return False
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
        self.initial["map_count"] = team.map_count
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
        team = Team.objects.filter(paymentid=paymentid, year=2023)[:1]
        if team and team.get().owner == user:
            return True

    def clean(self):
        cleaned_data = super().clean()

        # Capture whether any required-field errors existed before server guards run,
        # so the "fill all fields" banner is only shown for those (not for range
        # violations which have their own field-level messages).
        had_errors_before_guards = bool(self.errors)

        # Server-side guards: the segmented control / maps stepper cap values
        # in the browser only; enforce them here against a crafted POST.
        category_id = cleaned_data.get("category2_id")
        ucount = cleaned_data.get("ucount")
        category = (
            Category.objects.filter(id=category_id).select_related("race").first()
            if category_id
            else None
        )
        ucount_valid = False
        if ucount is not None:
            if not category:
                self.add_error("category2_id", "Выберите категорию.")
            elif not (category.min_people <= int(ucount) <= category.max_people):
                self.add_error(
                    "ucount",
                    "Недопустимое количество участников для выбранной категории.",
                )
            else:
                ucount_valid = True

        if ucount_valid:
            map_count = cleaned_data.get("map_count") or 0
            max_maps = max(0, int(ucount) - FREE_MAPS)
            if int(map_count) > max_maps:
                self.add_error(
                    "map_count",
                    "Слишком много дополнительных карт для такого состава.",
                )

        # Capacity gate: лимиты гонки/категории. Суперюзер (bypass_limits)
        # пропускает. Занятость считается по paid_people (см. caveat в плане).
        if ucount_valid and not self.bypass_limits:
            team = self.team
            new_ucount = int(ucount)

            # Гонка — блокирует только рост состава.
            needed = new_ucount - team.paid_people
            race_remaining = category.race.remaining_people()
            if race_remaining is not None and needed > 0 and needed > race_remaining:
                self.add_error(
                    "ucount",
                    f"В гонке закончились места: осталось {int(race_remaining)}.",
                )

            # Категория — блокирует вход в полную и рост в полной.
            cat_remaining = category.remaining_people(exclude_team=team)
            moving_in = category.id != team.category2_id
            growing = new_ucount > team.paid_people
            if (
                cat_remaining is not None
                and (moving_in or growing)
                and new_ucount > cat_remaining
            ):
                self.add_error(
                    "category2_id",
                    f"В категории нет мест: осталось {int(cat_remaining)}.",
                )

        # make invalid fields red:
        if self.errors:
            for f_name in self.fields:
                if f_name in self.errors:
                    classes = self.fields[f_name].widget.attrs.get("class", "")
                    classes += " is-invalid"
                    self.fields[f_name].widget.attrs["class"] = classes
            if had_errors_before_guards:
                raise forms.ValidationError("Заполните все поля")
        return cleaned_data

    @staticmethod
    def clean_birth(birth):
        if not birth:
            return "0"
        if not birth.isdigit():
            raise forms.ValidationError("Год рождения должен быть числом.")
        return birth

    def clean_birth1(self):
        return self.clean_birth(self.cleaned_data["birth1"])

    def clean_birth2(self):
        return self.clean_birth(self.cleaned_data["birth2"])

    def clean_birth3(self):
        return self.clean_birth(self.cleaned_data["birth3"])

    def clean_birth4(self):
        return self.clean_birth(self.cleaned_data["birth4"])

    def clean_birth5(self):
        return self.clean_birth(self.cleaned_data["birth5"])

    def clean_birth6(self):
        return self.clean_birth(self.cleaned_data["birth6"])

    def clean_map_count(self):
        value = self.cleaned_data.get("map_count")
        if value is None:
            return 0
        return value

    def save(self):
        if "paymentid" not in self.cleaned_data:
            return False
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2023)[:1]
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
            team.map_count = d.get("map_count", 0)
            team.category2 = category2_from_dist(team.dist, team.ucount)
            team.save()
            return team
        return False


class TeamMemberMoveForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        race_id = kwargs.pop("race_id", None)
        super().__init__(*args, **kwargs)
        if race_id:
            self.fields["to_team"].queryset = Team.objects.filter(
                category2__race_id=race_id
            ).order_by("id")
        self.fields["to_team"].label = "Команда назначения"
        self.fields["moved_people"].label = "Количество переносимых участников"
        self.fields["moved_people"].widget.attrs["min"] = 1

    class Meta:
        model = TeamMemberMove
        fields = ["from_team", "to_team", "moved_people"]
        widgets = {
            "from_team": forms.Select(attrs={"class": "form-control"}),
            "to_team": forms.Select(attrs={"class": "form-control"}),
            "moved_people": forms.NumberInput(
                attrs={"class": "form-control", "value": 1}
            ),
        }

    def clean_moved_people(self):
        moved = self.cleaned_data.get("moved_people")
        from_team = self.cleaned_data.get("from_team")
        if (
            moved is not None
            and from_team is not None
            and moved > from_team.paid_people
        ):
            raise forms.ValidationError(
                "Moved people cannot exceed the number of paid people in the from team."
            )
        return moved

    def clean(self):
        from_team = self.cleaned_data.get("from_team")
        to_team = self.cleaned_data.get("to_team")
        if from_team and to_team:
            if from_team.category2.race_id != to_team.category2.race_id:
                raise forms.ValidationError("Teams must be in the same Race")
        return super().clean()


class TeamFormAdmin(forms.Form):
    paymentid = forms.CharField(widget=forms.HiddenInput())
    get_package = forms.BooleanField(
        required=False,
        label="Получили стартовый пакет",
        widget=forms.CheckboxInput(
            attrs={
                "class": "custom-control-input",
            }
        ),
    )
    get_number = forms.BooleanField(
        required=False,
        label="Получили номер",
        widget=forms.CheckboxInput(
            attrs={
                "class": "custom-control-input",
            }
        ),
    )
    get_map = forms.BooleanField(
        required=False,
        label="Получили карту",
        widget=forms.CheckboxInput(
            attrs={
                "class": "custom-control-input",
            }
        ),
    )
    give_paper = forms.BooleanField(
        required=False,
        label="Сдали заявку",
        widget=forms.CheckboxInput(
            attrs={
                "class": "custom-control-input",
            }
        ),
    )
    give_photos = forms.BooleanField(
        required=False,
        label="Сдали фото",
        widget=forms.CheckboxInput(
            attrs={
                "class": "custom-control-input",
            }
        ),
    )
    dnf = forms.BooleanField(
        required=False,
        label="Не финишировали",
        widget=forms.CheckboxInput(
            attrs={
                "class": "custom-control-input",
            }
        ),
    )

    category = forms.ChoiceField(
        required=False,
        choices=(
            ("6h", "6 часов"),
            ("12h_mw", "12 часов МЖ"),
            ("12h_mm", "12 часов ММ"),
            ("12h_ww", "12 часов ЖЖ"),
            ("12h_team", "12 часов, команда"),
            ("24h", "24 часа"),
        ),
        label="Категория",
        widget=forms.Select(
            attrs={"class": "form-control form-control-lg", "placeholder": "Категория"}
        ),
    )
    start_number = forms.CharField(
        required=False,
        label="Стартовый номер",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Стартовый номер",
            }
        ),
    )
    start_time = forms.DateTimeField(
        required=False,
        label="Время старта",
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Время старта",
            }
        ),
    )
    finish_time = forms.DateTimeField(
        required=False,
        label="Время финиша",
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "Время финиша",
            }
        ),
    )
    penalty = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=1000,
        label="Штраф",
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-lg", "placeholder": "Штраф"}
        ),
    )

    def clean(self):
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2023)[:1]
        if not team:
            raise forms.ValidationError("Команда не найдена.")
        return self.cleaned_data

    def init_vals(self, user, paymentid=""):
        team = None
        if paymentid and user.is_superuser:
            team = Team.objects.filter(paymentid=paymentid, year=2023)[:1]
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
        self.initial["start_time"] = (
            team.start_time + timedelta(hours=5) if team.start_time else None
        )
        self.initial["finish_time"] = (
            team.finish_time + timedelta(hours=5) if team.finish_time else None
        )
        self.initial["penalty"] = team.penalty
        self.initial["dnf"] = team.dnf

    def save(self, user):
        if "paymentid" not in self.cleaned_data:
            return False
        paymentid = self.cleaned_data["paymentid"]
        team = Team.objects.filter(paymentid=paymentid, year=2023)[:1]
        if team:
            d = self.cleaned_data
            print(d)
            team = team.get()
            if "get_package" in d:
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
            if "start_number" in d and d["start_number"]:
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
            team_admin_log.paymentid = team.paymentid
            team_admin_log.get_package = team.get_package
            team_admin_log.get_number = team.get_number
            team_admin_log.get_map = team.get_map
            team_admin_log.give_paper = team.give_paper
            team_admin_log.give_photos = team.give_photos
            team_admin_log.category = team.category
            team_admin_log.start_number = team.start_number
            team_admin_log.start_time = team.start_time
            team_admin_log.finish_time = team.finish_time
            team_admin_log.distance_time = team.distance_time
            team_admin_log.penalty = team.penalty
            team_admin_log.dnf = team.dnf
            team_admin_log.save()
            return team
        return False


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ["title", "content"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Заголовок страницы"}
            ),
            "content": forms.Textarea(
                attrs={"class": "form-control", "rows": 20, "placeholder": "Markdown"}
            ),
        }


class NewsPostForm(forms.ModelForm):
    class Meta:
        model = NewsPost
        fields = ["title", "content", "image"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Заголовок"}
            ),
            "content": forms.Textarea(
                attrs={"class": "form-control", "rows": 6, "placeholder": "Markdown"}
            ),
        }


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
