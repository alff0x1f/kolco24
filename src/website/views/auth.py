from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.urls import reverse_lazy

from ..forms import CustomPasswordResetForm, CustomSetPasswordForm


class CustomPasswordResetView(PasswordResetView):
    """Password Reset View"""

    template_name = "website/password_reset.html"
    form_class = CustomPasswordResetForm
    success_url = reverse_lazy("password_reset_done")
    subject_template_name = "registration/password_reset_subject.txt"
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "registration/password_reset_email.html"
    from_email = "Кольцо24 <org@kolco24.ru>"


class CustomPasswordResetDoneView(PasswordResetDoneView):
    """Password Reset Done View"""

    template_name = "website/password_reset_done.html"  # Success message template


#
class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    """Password Reset Confirm View"""

    form_class = CustomSetPasswordForm

    post_reset_login = True
    template_name = "website/password_reset_confirm.html"


class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    """Password Reset Complete View"""

    template_name = "website/password_reset_complete.html"
