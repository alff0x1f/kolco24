from django import forms

from website.models.race import Race


class RaceForm(forms.ModelForm):
    """ModelForm over the scalar ``Race`` fields.

    Widgets carry no ``attrs`` — the template renders every input manually
    (see the ``base-2.html`` convention). ``code``/``slug`` uniqueness is
    enforced by the model fields with automatic self-exclusion on edit, and
    ``Race.clean()`` (header image/logo URL validation) runs via the
    ModelForm's ``_post_clean``. The removed ``is_reg_open`` field is
    intentionally absent; ``cost`` is now only the fallback price.
    """

    class Meta:
        model = Race
        fields = [
            "name",
            "code",
            "slug",
            "place",
            "date",
            "date_end",
            "cost",
            "header_image",
            "header_logo",
            "reg_status",
            "is_active",
            "is_legend_visible",
            "is_teams_editable",
            "is_photo_upload_enabled",
        ]
