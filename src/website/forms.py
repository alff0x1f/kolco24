from django import forms

from apps.accounts.forms import (  # noqa: F401
    DUPLICATE_EMAIL_MSG,
    CustomPasswordResetForm,
    CustomSetPasswordForm,
    ImpersonateForm,
    LoginForm,
    RegForm,
)
from website.models import BreakfastRegistration, Team, TeamMemberMove, Transfer
from website.models.news import NewsPost, Page
from website.models.race import Category, Race

BUS_REGISTRATION_MAX_PASSENGERS = 20
BREAKFAST_MAX_ATTENDEES = 20
# Maps pricing — mirrored in the views and the JS config island.
FREE_MAPS = 2
MAP_PRICE = 200


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

        # Resolve the race defensively: callers may pass a bad/None race_id
        # (e.g. TeamForm(request.POST or None)). On a non-int id, expose no
        # add-ons and add no extra fields rather than raising.
        try:
            resolved_race_id = int(race_id)
        except (TypeError, ValueError):
            resolved_race_id = None

        self.extras = []
        # count_paid per RaceExtra id, used by clean() to block reductions.
        self._extra_paid = {}
        if resolved_race_id is not None:
            from apps.race.models import RaceExtra

            self.extras = list(
                RaceExtra.objects.filter(race_id=resolved_race_id, is_active=True)
            )
            team_counts = {}
            if self.team and self.team.pk:
                for te in self.team.extras.all():
                    team_counts[te.race_extra_id] = te.count
                    self._extra_paid[te.race_extra_id] = te.count_paid
            for extra in self.extras:
                self.fields[f"extra_{extra.code}"] = forms.IntegerField(
                    min_value=0,
                    required=False,
                    initial=team_counts.get(extra.id, 0),
                    widget=forms.HiddenInput(),
                    label=extra.name,
                )

        cats = list(
            Category.objects.filter(race_id=resolved_race_id, is_active=True)
            if resolved_race_id is not None
            else []
        )
        if current_category_id is not None and resolved_race_id is not None:
            active_ids = {c.id for c in cats}
            if current_category_id not in active_ids:
                current_cat = Category.objects.filter(
                    id=current_category_id, race_id=resolved_race_id
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

    def clean(self):
        cleaned_data = super().clean()

        # Capture whether any required-field errors existed before server guards run,
        # so the "fill all fields" banner is only shown for those (not for range
        # violations which have their own field-level messages).
        had_errors_before_guards = bool(self.errors)

        # Server-side guards: the segmented control / add-on steppers cap
        # values in the browser only; enforce them here against a crafted POST.
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
            # Per add-on caps: count ≤ max(0, ucount − free_per_team); on edit
            # the count cannot drop below what's already paid.
            for extra in self.extras:
                field = f"extra_{extra.code}"
                count = int(cleaned_data.get(field) or 0)
                max_count = max(0, int(ucount) - extra.free_per_team)
                if count > max_count:
                    self.add_error(
                        field,
                        f"Слишком много «{extra.name}» для такого состава.",
                    )
                    continue
                count_paid = self._extra_paid.get(extra.id, 0)
                if count < count_paid:
                    self.add_error(
                        field,
                        f"Нельзя уменьшить «{extra.name}»: часть оплачена.",
                    )

        # Capacity gate: лимиты гонки/категории. Суперюзер (bypass_limits)
        # пропускает. Занятость считается по paid_people (см. caveat в плане).
        if ucount_valid and not self.bypass_limits:
            team = self.team
            new_ucount = int(ucount)

            # Гонка — блокирует только рост состава.
            needed = new_ucount - team.paid_people
            race_remaining = category.race.remaining_people(exclude_team=team)
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
