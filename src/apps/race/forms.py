from django import forms
from django.forms.widgets import DateInput

from website.models.race import Race


class RaceForm(forms.ModelForm):
    """ModelForm over the scalar ``Race`` fields.

    Widgets carry no ``attrs`` — the template renders every input manually
    (see the ``base-2.html`` convention). ``slug`` uniqueness is enforced by
    the model field with automatic self-exclusion on edit, and ``Race.clean()``
    (header image/logo URL validation) runs via the ModelForm's ``_post_clean``.
    The removed ``is_reg_open`` field is intentionally absent; ``cost`` is now
    only the fallback price.
    """

    cost = forms.IntegerField(required=False, min_value=0)
    people_limit = forms.IntegerField(required=False, min_value=0)
    # Add-ons («Доп-услуги») are posted as a JSON array in this hidden field and
    # reconciled by the view (see ``_reconcile_extras``), like categories and
    # price tiers. Not a model field — parsed/validated in ``RaceEditView.post``.
    extras_json = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_cost(self):
        v = self.cleaned_data.get("cost")
        return v if v is not None else 0

    def clean_people_limit(self):
        v = self.cleaned_data.get("people_limit")
        return v if v is not None else 0

    def clean(self):
        cleaned = super().clean()
        date = cleaned.get("date")
        date_end = cleaned.get("date_end")
        if date and date_end and date_end < date:
            self.add_error(
                "date_end", "Дата окончания не может быть раньше даты начала."
            )
        return cleaned

    class Meta:
        model = Race
        widgets = {
            "date": DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "date_end": DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }
        fields = [
            "name",
            "slug",
            "place",
            "date",
            "date_end",
            "cost",
            "people_limit",
            "header_image",
            "header_logo",
            "reg_status",
            "is_active",
            "is_legend_visible",
            "is_teams_editable",
            "is_photo_upload_enabled",
        ]
