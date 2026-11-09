from django import forms
from django.forms.widgets import DateInput

from apps.photo_contest.images import ImageRejected
from apps.race.header_images import parse_crop, process_cover, process_logo
from website.models.race import Race

HEADER_UPLOADS = (("header_image", process_cover), ("header_logo", process_logo))


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
    # Promo codes («Промокоды») — same hidden-JSON pattern as add-ons; parsed
    # and validated in ``RaceEditView.post`` (see ``_reconcile_promos``).
    promos_json = forms.CharField(required=False, widget=forms.HiddenInput)
    header_image_file = forms.FileField(required=False)
    header_logo_file = forms.FileField(required=False)
    header_image_crop = forms.CharField(required=False, widget=forms.HiddenInput)
    header_logo_crop = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.processed_uploads = {}

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
        self._process_uploads(cleaned)
        return cleaned

    def _process_uploads(self, cleaned):
        # Runs in clean(), not clean_<file>(): extra form fields are cleaned in
        # declaration order, so the crop may not be in cleaned_data yet.
        for field, process in HEADER_UPLOADS:
            file = cleaned.get(f"{field}_file")
            if not file:
                continue
            try:
                crop = parse_crop(cleaned.get(f"{field}_crop"))
                self.processed_uploads[field] = process(file, crop)
            except ImageRejected as exc:
                self.add_error(f"{field}_file", str(exc))
                continue
            # The file replaces the text value, so a stale bad URL can't block
            # the save in Race.clean().
            cleaned[field] = ""
            self.errors.pop(field, None)

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
            "map_url",
            "reg_status",
            "is_published",
            "is_teams_editable",
            "is_photo_upload_enabled",
        ]
