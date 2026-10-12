from django import forms

from apps.photo_contest import images


class PhotoUploadForm(forms.Form):
    nomination = forms.ModelChoiceField(
        queryset=None,
        error_messages={
            "required": "Выберите номинацию.",
            "invalid_choice": "Выберите номинацию из списка.",
        },
    )
    image = forms.FileField(error_messages={"required": "Выберите файл."})
    caption = forms.CharField(
        max_length=300,
        required=False,
        error_messages={"max_length": "Не больше 300 символов."},
    )

    def __init__(self, *args, contest, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nomination"].queryset = contest.nominations.all()
        self.image_info = None

    def clean_image(self):
        file = self.cleaned_data["image"]
        try:
            self.image_info = images.probe(file)
        except images.ImageRejected as exc:
            raise forms.ValidationError(str(exc))
        return file
