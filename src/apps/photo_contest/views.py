from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import (
    FileResponse,
    Http404,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views import View

from apps.accounts.users import find_user
from apps.photo_contest import images
from apps.photo_contest.forms import PhotoUploadForm
from apps.photo_contest.models import PhotoContest, PhotoContestModerator, PhotoEntry
from apps.photo_contest.permissions import can_moderate
from apps.photo_contest.services import LimitReached, SubmissionClosed, create_entry

CLOSED_MESSAGE = "Приём фотографий закрыт."
MODERATE_PAGE_SIZE = 48
STATUS_ALL = "all"
STATUS_TABS = [*PhotoEntry.STATUS_CHOICES, (STATUS_ALL, "Все")]
MODERATE_ACTIONS = [
    (PhotoEntry.APPROVED, "Принять"),
    (PhotoEntry.REJECTED, "Отклонить"),
    (PhotoEntry.PENDING, "Вернуть на модерацию"),
]


def _login_redirect(contest):
    next_url = reverse("photo_contest", args=[contest.slug])
    return HttpResponseRedirect(reverse("login") + "?" + urlencode({"next": next_url}))


def _visible_contest(request, slug):
    """Return ``(contest, is_moderator)``; unpublished is 404 for outsiders."""
    contest = get_object_or_404(PhotoContest, slug=slug)
    is_moderator = can_moderate(request.user, contest)
    user = request.user
    if not contest.is_published and not (
        user.is_staff or user.is_superuser or is_moderator
    ):
        raise Http404
    return contest, is_moderator


def _contest_context(request, contest, form=None, is_moderator=None):
    user = request.user
    if is_moderator is None:
        is_moderator = can_moderate(user, contest)
    nominations = list(contest.nominations.all())
    for nomination in nominations:
        nomination.used = 0
        nomination.is_full = False
        nomination.my_entries = []
    my_entries = []
    if user.is_authenticated:
        my_entries = list(
            PhotoEntry.objects.filter(nomination__contest=contest, uploaded_by=user)
            .select_related("nomination")
            .order_by("nomination__order", "nomination__pk", "-created_at")
        )
        by_pk = {n.pk: n for n in nominations}
        for entry in my_entries:
            nomination = by_pk[entry.nomination_id]
            nomination.my_entries.append(entry)
            if entry.status != PhotoEntry.REJECTED:
                nomination.used += 1
        for nomination in nominations:
            nomination.is_full = nomination.used >= contest.max_photos_per_user

    form = form or PhotoUploadForm(contest=contest)
    open_pk = None
    if form.is_bound:
        raw = str(form.data.get("nomination", ""))
        open_pk = next((n.pk for n in nominations if str(n.pk) == raw), None)
    return {
        "contest": contest,
        "nominations": nominations,
        "my_entries": my_entries,
        "form": form,
        "open_nomination_pk": open_pk,
        "orphan_errors": form.is_bound and bool(form.errors) and open_pk is None,
        "accept": "image/jpeg,image/png,image/webp",
        **_moderators_context(user, contest, is_moderator),
    }


def _moderators_context(user, contest, is_moderator):
    can_manage = user.is_superuser
    context = {
        "can_moderate": is_moderator,
        "can_manage_moderators": can_manage,
        "show_moderators": can_manage or is_moderator,
        "moderators": [],
        "pending_count": 0,
    }
    if context["show_moderators"]:
        context["moderators"] = [
            {
                "user_id": row.user_id,
                "name": row.user.get_full_name() or row.user.get_username(),
                "email": row.user.email,
            }
            for row in contest.moderators.select_related("user")
        ]
    if is_moderator:
        context["pending_count"] = PhotoEntry.objects.filter(
            nomination__contest=contest, status=PhotoEntry.PENDING
        ).count()
    return context


class PhotoContestListView(View):
    def get(self, request):
        contests = list(PhotoContest.objects.filter(is_published=True))
        now = timezone.now()
        open_contests = sorted(
            (c for c in contests if c.submission_until >= now),
            key=lambda c: c.submission_until,
        )
        closed_contests = [c for c in contests if c.submission_until < now]
        return render(
            request,
            "photo_contest/list.html",
            {"open_contests": open_contests, "closed_contests": closed_contests},
        )


class PhotoContestView(View):
    def get(self, request, slug):
        contest, is_moderator = _visible_contest(request, slug)
        return render(
            request,
            "photo_contest/contest.html",
            _contest_context(request, contest, is_moderator=is_moderator),
        )


class PhotoUploadView(View):
    def post(self, request, slug):
        contest = get_object_or_404(PhotoContest, slug=slug, is_published=True)
        if not request.user.is_authenticated:
            return _login_redirect(contest)
        if not contest.is_submission_open:
            messages.error(request, CLOSED_MESSAGE)
            return redirect("photo_contest", contest.slug)

        form = PhotoUploadForm(request.POST, request.FILES, contest=contest)
        if form.is_valid():
            try:
                create_entry(
                    form.cleaned_data["nomination"],
                    request.user,
                    form.cleaned_data["image"],
                    form.image_info,
                    form.cleaned_data["caption"],
                )
            except images.ImageRejected as exc:
                form.add_error("image", str(exc))
            except LimitReached as exc:
                form.add_error("nomination", str(exc))
            except SubmissionClosed:
                messages.error(request, CLOSED_MESSAGE)
                return redirect("photo_contest", contest.slug)
            else:
                messages.success(request, "Фото отправлено на модерацию.")
                url = reverse("photo_contest", args=[contest.slug])
                nomination_pk = form.cleaned_data["nomination"].pk
                return HttpResponseRedirect(f"{url}#nomination-{nomination_pk}")
        return render(
            request,
            "photo_contest/contest.html",
            _contest_context(request, contest, form),
        )


class PhotoDeleteView(View):
    def post(self, request, slug, entry_id):
        contest = get_object_or_404(PhotoContest, slug=slug, is_published=True)
        if not request.user.is_authenticated:
            return _login_redirect(contest)
        entry = get_object_or_404(
            PhotoEntry,
            pk=entry_id,
            nomination__contest=contest,
            uploaded_by=request.user,
        )
        if not contest.is_submission_open:
            messages.error(request, CLOSED_MESSAGE)
        else:
            entry.delete()
            messages.success(request, "Фото удалено.")
        return redirect("photo_contest", contest.slug)


class PhotoOriginalView(View):
    def get(self, request, entry_id):
        # 404, not 403: don't reveal which entries exist.
        user = request.user
        if not user.is_authenticated:
            raise Http404
        entry = get_object_or_404(
            PhotoEntry.objects.select_related("nomination"), pk=entry_id
        )
        if not user.has_perm("photo_contest.view_photoentry") and not (
            PhotoContestModerator.objects.filter(
                contest_id=entry.nomination.contest_id, user=user
            ).exists()
        ):
            raise Http404
        try:
            file = entry.original.open("rb")
        except FileNotFoundError:
            raise Http404
        return FileResponse(file)


def _moderated_contest(request, slug):
    """Return ``(contest, response)``; return a non-``None`` response as is."""
    contest = get_object_or_404(PhotoContest, slug=slug)
    if not request.user.is_authenticated:
        next_url = reverse("photo_moderate", args=[contest.slug])
        login_url = reverse("login") + "?" + urlencode({"next": next_url})
        return contest, HttpResponseRedirect(login_url)
    if not can_moderate(request.user, contest):
        return contest, HttpResponseForbidden()
    return contest, None


class PhotoModerateView(View):
    def get(self, request, slug):
        contest, response = _moderated_contest(request, slug)
        if response:
            return response

        nominations = list(contest.nominations.all())
        raw_nomination = request.GET.get("nomination", "")
        nomination = next((n for n in nominations if str(n.pk) == raw_nomination), None)
        status = request.GET.get("status", PhotoEntry.PENDING)
        if status not in dict(STATUS_TABS):
            status = PhotoEntry.PENDING

        entries = PhotoEntry.objects.filter(nomination__contest=contest)
        if nomination:
            entries = entries.filter(nomination=nomination)
        counts = dict(entries.values_list("status").annotate(Count("pk")).order_by())
        counts[STATUS_ALL] = sum(counts.values())
        if status != STATUS_ALL:
            entries = entries.filter(status=status)
        entries = entries.select_related("nomination", "uploaded_by").order_by(
            "created_at", "pk"
        )
        page = Paginator(entries, MODERATE_PAGE_SIZE).get_page(request.GET.get("page"))
        page_entries = list(page)
        for entry in page_entries:
            entry.megapixels = entry.width * entry.height / 1_000_000
            entry.size_mb = entry.size_bytes / (1024 * 1024)

        # Django 4.2 has no {% querystring %}, so links are built here.
        base_url = reverse("photo_moderate", args=[contest.slug])

        def url(**params):
            query = {"status": status, "nomination": nomination and nomination.pk}
            query.update(params)
            query = {k: v for k, v in query.items() if v}
            return f"{base_url}?{urlencode(query)}"

        return render(
            request,
            "photo_contest/moderate.html",
            {
                "contest": contest,
                "status": status,
                "nomination": nomination,
                "tabs": [
                    {
                        "value": value,
                        "label": label,
                        "count": counts.get(value, 0),
                        "url": url(status=value),
                    }
                    for value, label in STATUS_TABS
                ],
                "nomination_links": [
                    {
                        "title": n.title if n else "Все номинации",
                        "url": url(nomination=n and n.pk),
                        "active": n == nomination,
                    }
                    for n in [None, *nominations]
                ],
                "page": page,
                "page_entries": page_entries,
                "prev_url": page.has_previous()
                and url(page=page.previous_page_number()),
                "next_url": page.has_next() and url(page=page.next_page_number()),
                "actions": MODERATE_ACTIONS,
            },
        )


class PhotoModerateEntryView(View):
    def post(self, request, slug, entry_id):
        contest, response = _moderated_contest(request, slug)
        if response:
            return response
        entry = get_object_or_404(PhotoEntry, pk=entry_id, nomination__contest=contest)
        status = request.POST.get("status")
        if status not in dict(PhotoEntry.STATUS_CHOICES):
            return HttpResponseBadRequest()
        # The per-nomination limit is deliberately not re-checked: restoring a
        # rejected photo may put its author over the limit, as in /admin/.
        entry.status = status
        entry.save(update_fields=["status"])

        next_url = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = reverse("photo_moderate", args=[contest.slug])
        return HttpResponseRedirect(next_url)


def _user_id(value):
    """Coerce a posted user id to ``int``; ``None`` when unparseable or out of range."""
    try:
        user_id = int(value)
    except (TypeError, ValueError):
        return None
    return user_id if 0 < user_id < 2**63 else None


class PhotoContestModeratorsView(View):
    """Superuser-only POST that adds or removes a contest's moderators."""

    MESSAGE_TAG = "contest-moderators"
    MAX_QUERY_LENGTH = 254

    def post(self, request, slug):
        contest = get_object_or_404(PhotoContest, slug=slug)
        contest_url = reverse("photo_contest", args=[contest.slug])
        if not request.user.is_authenticated:
            return HttpResponseRedirect(
                reverse("login") + "?" + urlencode({"next": contest_url})
            )
        if not request.user.is_superuser:
            return HttpResponseForbidden()
        action = request.POST.get("action")
        if action == "add":
            self._add(request, contest)
        elif action == "remove":
            self._remove(request, contest)
        else:
            return HttpResponseBadRequest()
        return HttpResponseRedirect(contest_url + "#contest-moderators")

    def _message(self, request, level, text):
        messages.add_message(request, level, text, extra_tags=self.MESSAGE_TAG)

    def _add(self, request, contest):
        query = request.POST.get("user", "").strip()
        if not query:
            self._message(request, messages.ERROR, "Укажите email или ID пользователя.")
            return
        if len(query) > self.MAX_QUERY_LENGTH:
            self._message(request, messages.ERROR, "Слишком длинное значение.")
            return
        user = find_user(query)
        if user is None:
            self._message(request, messages.ERROR, f"Пользователь «{query}» не найден.")
            return
        name = user.get_full_name() or user.get_username()
        _, created = PhotoContestModerator.objects.get_or_create(
            contest=contest, user=user
        )
        if created:
            self._message(
                request, messages.SUCCESS, f"{name} назначен(а) администратором."
            )
        else:
            self._message(request, messages.SUCCESS, f"{name} уже администратор.")

    def _remove(self, request, contest):
        deleted, _ = PhotoContestModerator.objects.filter(
            contest=contest, user_id=_user_id(request.POST.get("user_id"))
        ).delete()
        if deleted:
            self._message(request, messages.SUCCESS, "Администратор снят.")
        else:
            self._message(request, messages.ERROR, "Администратор не найден.")
