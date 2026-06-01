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
from django.core.signing import BadSignature, TimestampSigner
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.accounts.emails import send_login_email
from apps.accounts.forms import (
    DUPLICATE_EMAIL_MSG,
    CodeForm,
    CustomPasswordResetForm,
    CustomSetPasswordForm,
    EmailStartForm,
    ImpersonateForm,
    LoginForm,
    RegForm,
)
from apps.accounts.models import EmailVerification
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


def _username_from_email(email):
    """Derive a unique username from the email local-part, deduping with a suffix."""
    base = email.split("@", 1)[0][:150] or "user"
    username = base
    i = 1
    while User.objects.filter(username=username).exists():
        i += 1
        suffix = f"-{i}"
        username = base[: 150 - len(suffix)] + suffix
    return username


def _complete_login(request, email, next_url):
    """Shared join point for the code path and the magic-link path.

    Logs in the existing user for ``email`` or creates the account inline (with an
    unguessable password), then safe-redirects to ``next_url``. The caller is
    responsible for marking the verification row consumed first.
    """
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    _username_from_email(email), email, get_random_string(32)
                )
        except IntegrityError:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                # Username collision with a concurrent user sharing the same local-part.
                # Fall back to a random username that won't collide.
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            get_random_string(12), email, get_random_string(32)
                        )
                except IntegrityError:
                    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return _safe_redirect(request, "/accounts/start/")
    auth_login(request, user, backend="apps.accounts.backends.EmailBackend")
    return _safe_redirect(request, next_url or "/")


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


PENDING_EMAIL_KEY = "accounts_pending_email"
PENDING_NEXT_KEY = "accounts_next"


class StartView(View):
    """Email-first entry: collect the email and send a code + magic link."""

    template = "accounts/start.html"

    def get(self, request):
        if request.user.is_authenticated:
            return _safe_redirect(request, request.GET.get("next", "/"))
        form = EmailStartForm()
        return render(
            request,
            self.template,
            {"form": form, "next": request.GET.get("next", "")},
        )

    def post(self, request):
        next_url = request.POST.get("next") or request.GET.get("next", "")
        if request.user.is_authenticated:
            return _safe_redirect(request, next_url or "/")
        form = EmailStartForm(request.POST)
        if not form.is_valid():
            return render(request, self.template, {"form": form, "next": next_url})

        email = form.cleaned_data["email"]
        verification, raw_code = EmailVerification.create_for(email)
        # raw_code is None within the resend cooldown — stay neutral, send nothing.
        if raw_code is not None:
            send_login_email(request, verification, raw_code, next_url)

        request.session[PENDING_EMAIL_KEY] = verification.email
        request.session[PENDING_NEXT_KEY] = next_url
        return HttpResponseRedirect(reverse("account_verify"))


class VerifyView(View):
    """Show the code field; verify a code (or resend) and complete the login."""

    template = "accounts/verify.html"

    def get(self, request):
        if request.user.is_authenticated:
            return _safe_redirect(request, request.GET.get("next", "/"))
        email = request.session.get(PENDING_EMAIL_KEY)
        if not email:
            return HttpResponseRedirect(reverse("account_start"))
        return render(
            request,
            self.template,
            {
                "form": CodeForm(),
                "email": email,
                "next": request.session.get(PENDING_NEXT_KEY, ""),
            },
        )

    def post(self, request):
        if request.user.is_authenticated:
            return _safe_redirect(request, request.session.get(PENDING_NEXT_KEY) or "/")
        email = request.session.get(PENDING_EMAIL_KEY)
        if not email:
            return HttpResponseRedirect(reverse("account_start"))
        next_url = request.session.get(PENDING_NEXT_KEY, "")

        if request.POST.get("action") == "resend":
            verification, raw_code = EmailVerification.create_for(email)
            if raw_code is not None:
                send_login_email(request, verification, raw_code, next_url)
                messages.success(request, "Письмо отправлено повторно.")
            else:
                messages.info(request, "Письмо уже отправлено, проверьте почту.")
            return HttpResponseRedirect(reverse("account_verify"))

        form = CodeForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template,
                {"form": form, "email": email, "next": next_url},
            )

        verification = (
            EmailVerification.objects.filter(
                email=email, purpose="login", consumed_at__isnull=True
            )
            .order_by("-created_at")
            .first()
        )
        if verification is None or not verification.atomic_consume_if_valid(
            form.cleaned_data["code"]
        ):
            messages.error(request, "Неверный или устаревший код. Попробуйте ещё раз.")
            return render(
                request,
                self.template,
                {"form": form, "email": email, "next": next_url},
            )

        request.session.pop(PENDING_EMAIL_KEY, None)
        request.session.pop(PENDING_NEXT_KEY, None)
        return _complete_login(request, verification.email, next_url)


class MagicLinkView(View):
    """Magic-link login entry point.

    Unsigns the verification pk from the URL (a tampered signature 404s), checks the
    row is alive (``expires_at``/``consumed_at``/attempts), then completes the login.
    Does NOT rely on the session — the link may open in a different browser.
    """

    def get(self, request, signed, *args, **kwargs):
        try:
            pk = TimestampSigner().unsign(signed, max_age=EmailVerification.CODE_TTL)
        except BadSignature:
            raise Http404("Invalid link")
        verification = EmailVerification.objects.filter(pk=pk).first()
        if verification is None or not verification.is_alive:
            raise Http404("Invalid link")
        next_url = request.GET.get("next", "")
        if not verification.mark_consumed_atomic():
            raise Http404("Invalid link")
        return _complete_login(request, verification.email, next_url)


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
