import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.accounts.forms import (
    DUPLICATE_EMAIL_MSG,
    CustomPasswordResetForm,
    CustomSetPasswordForm,
    ImpersonateForm,
    LoginForm,
    RegForm,
)
from website.models import Race
from website.models.race import RegStatus

logger = logging.getLogger(__name__)


def _get_auth_backend():
    backends = getattr(settings, "AUTHENTICATION_BACKENDS", [])
    if backends:
        return backends[0]
    return "django.contrib.auth.backends.ModelBackend"


def _safe_redirect(request, url):
    if url and url_has_allowed_host_and_scheme(
        url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return HttpResponseRedirect(url)
    return HttpResponseRedirect("/")


def _find_user_for_impersonation(query):
    user = None
    if query.isdigit():
        user = User.objects.filter(pk=int(query)).first()
    if not user:
        user = User.objects.filter(email__iexact=query).first()
    if not user:
        user = User.objects.filter(username__iexact=query).first()
    return user


def _login_without_credentials(request, user):
    auth_login(request, user, backend=_get_auth_backend())


def _mark_field_invalid(field):
    classes = field.widget.attrs.get("class", "").split()
    if "is-invalid" not in classes:
        classes.append("is-invalid")
        field.widget.attrs["class"] = " ".join(filter(None, classes))


class RegisterView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return HttpResponseRedirect("/")
        context = self.get_context()
        context["next"] = request.GET.get("next", "")
        return render(request, "accounts/register.html", context)

    def post(self, request):
        next_url = request.POST.get("next") or request.GET.get("next", "")
        form = RegForm(request.POST)
        if form.is_valid():
            # Extract cleaned form data
            first_name = form.cleaned_data["first_name"]
            last_name = form.cleaned_data["last_name"]
            email = form.cleaned_data["email"]
            phone = form.cleaned_data["phone"]
            password = form.cleaned_data["password"]

            username = f"{last_name}, {first_name}"
            if User.objects.filter(username=username).exists():
                username = self.get_next_username(first_name, last_name)

            try:
                with transaction.atomic():
                    user = User.objects.create_user(username, email, password)
                    user.first_name = first_name
                    user.last_name = last_name
                    user.profile.phone = phone
                    user.save(update_fields=("first_name", "last_name"))
            except IntegrityError:
                if User.objects.filter(email__iexact=email).exists():
                    form.add_error("email", DUPLICATE_EMAIL_MSG)
                    return render(
                        request,
                        "accounts/register.html",
                        {"reg_form": form, "next": next_url},
                    )
                raise

            auth_login(request, user)

            # A guest who started from a login-gated page (e.g. «Войти и
            # добавить команду») carries ?next= through login → register; honor
            # it so they land back where they came from.
            if next_url:
                return _safe_redirect(request, next_url)

            # todo change it in 2026
            race = Race.objects.filter(id=8).first()  # 2025
            if race is None:
                return HttpResponseRedirect("/")
            if race.reg_status == RegStatus.OPEN:
                return HttpResponseRedirect(reverse("add_team", args=[race.slug]))
            return HttpResponseRedirect(reverse("my_teams", args=[race.slug]))

        return render(
            request,
            "accounts/register.html",
            {"reg_form": form, "next": next_url},
        )

    @staticmethod
    def get_next_username(first_name, last_name):
        i = 2
        new_username = f"{last_name}, {first_name} {i}"
        while User.objects.filter(username=new_username).exists():
            i += 1
            new_username = f"{last_name}, {first_name} {i}"
        return new_username

    def get_context(self):
        return {
            "reg_form": RegForm(),
            "reg_open": settings.REG_OPEN,
        }


class LoginView(View):
    template = "accounts/login.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return _safe_redirect(request, request.GET.get("next", "/"))
        form = LoginForm()
        return render(
            request,
            self.template,
            {"form": form, "next": request.GET.get("next", "")},
        )

    def post(self, request, *args, **kwargs):
        next_url = request.POST.get("next") or request.GET.get("next", "/")
        if request.user.is_authenticated:
            return _safe_redirect(request, next_url)
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get("email")
            password = form.cleaned_data.get("password")

            user = authenticate(request, username=email, password=password)

            if user is not None:
                auth_login(request, user)
                return _safe_redirect(request, next_url)
            else:
                messages.error(
                    request, "Неправильный email или пароль. Попробуйте снова."
                )
        return render(request, self.template, {"form": form, "next": next_url})


class LogoutUserView(View):
    def post(self, request, *args, **kwargs):
        if request.POST.get("logout", "") == "logout":
            if request.user.is_authenticated:
                logout(request)
        return HttpResponseRedirect("/")


def impersonate(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect(reverse("login") + f"?next={request.path}")

    # Check if the original user (either current user or impersonator) is a superuser
    original_user_id = request.session.get("impersonator_id") or request.user.pk
    try:
        original_user = User.objects.get(pk=original_user_id)
    except User.DoesNotExist:
        raise Http404("Not found")
    if not original_user.is_superuser:
        raise Http404("Not found")
    initial_next = request.GET.get("next")
    if not initial_next:
        referer = request.META.get("HTTP_REFERER", "")
        if referer and url_has_allowed_host_and_scheme(
            referer,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            initial_next = referer

    form = ImpersonateForm(request.POST or None, initial={"next": initial_next})

    if request.method == "POST":
        if form.is_valid():
            query = form.cleaned_data["query"]
            target_user = _find_user_for_impersonation(query)

            if not target_user:
                form.add_error("query", "Пользователь не найден.")
                _mark_field_invalid(form.fields["query"])
            else:
                if target_user.pk == request.user.pk:
                    form.add_error("query", "Вы уже вошли под этим пользователем.")
                    _mark_field_invalid(form.fields["query"])
                elif target_user.pk == original_user_id:
                    # Switching back to the original user: stop impersonation
                    _login_without_credentials(request, original_user)
                    request.session.pop("impersonator_id", None)
                    next_url = form.cleaned_data.get("next")
                    return _safe_redirect(request, next_url)
                else:
                    _login_without_credentials(request, target_user)
                    request.session["impersonator_id"] = original_user_id
                    next_url = form.cleaned_data.get("next")
                    return _safe_redirect(request, next_url)
        else:
            _mark_field_invalid(form.fields["query"])

    return render(request, "accounts/impersonate.html", {"form": form})


def stop_impersonate(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect("/")

    original_user_id = request.session.get("impersonator_id")

    if not original_user_id:
        return HttpResponseRedirect("/")

    try:
        original_user = User.objects.get(pk=original_user_id)
    except User.DoesNotExist:
        request.session.pop("impersonator_id", None)
        return HttpResponseRedirect("/")

    _login_without_credentials(request, original_user)
    request.session.pop("impersonator_id", None)

    next_url = request.GET.get("next")
    return _safe_redirect(request, next_url)


class MagicLinkView(View):
    """Magic-link login entry point.

    Placeholder: the route exists so ``send_login_email`` can ``reverse`` it; the
    full unsign/login logic is implemented in a later task.
    """

    def get(self, request, signed, *args, **kwargs):
        raise Http404("Not implemented yet")


class CustomPasswordResetView(PasswordResetView):
    """Password Reset View"""

    template_name = "accounts/password_reset.html"
    form_class = CustomPasswordResetForm
    success_url = reverse_lazy("password_reset_done")
    subject_template_name = "registration/password_reset_subject.txt"
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "registration/password_reset_email.html"
    from_email = "Кольцо24 <org@kolco24.ru>"


class CustomPasswordResetDoneView(PasswordResetDoneView):
    """Password Reset Done View"""

    template_name = "accounts/password_reset_done.html"  # Success message template


class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    """Password Reset Confirm View"""

    form_class = CustomSetPasswordForm

    post_reset_login = True
    template_name = "accounts/password_reset_confirm.html"


class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    """Password Reset Complete View"""

    template_name = "accounts/password_reset_complete.html"
