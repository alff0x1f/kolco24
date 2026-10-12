from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views import View

from apps.photo_contest import images
from apps.photo_contest.forms import PhotoUploadForm
from apps.photo_contest.models import PhotoContest, PhotoEntry
from apps.photo_contest.services import LimitReached, create_entry

CLOSED_MESSAGE = "Приём фотографий закрыт."


def _login_redirect(contest):
    next_url = reverse("photo_contest", args=[contest.slug])
    return HttpResponseRedirect(
        reverse("account_start") + "?" + urlencode({"next": next_url})
    )


def _visible_contest(request, slug):
    contest = get_object_or_404(PhotoContest, slug=slug)
    if not contest.is_published and not request.user.is_staff:
        raise Http404
    return contest


def _contest_context(request, contest, form=None):
    user = request.user
    nominations = list(contest.nominations.all())
    for nomination in nominations:
        nomination.is_full = False
    my_entries = []
    if user.is_authenticated:
        used = dict(
            contest.nominations.annotate(
                used=Count(
                    "entries",
                    filter=Q(entries__uploaded_by=user)
                    & ~Q(entries__status=PhotoEntry.REJECTED),
                )
            ).values_list("pk", "used")
        )
        for nomination in nominations:
            nomination.is_full = used[nomination.pk] >= contest.max_photos_per_user
        my_entries = list(
            PhotoEntry.objects.filter(nomination__contest=contest, uploaded_by=user)
            .select_related("nomination")
            .order_by("nomination__order", "nomination__pk", "-created_at")
        )
    return {
        "contest": contest,
        "nominations": nominations,
        "all_full": bool(nominations) and all(n.is_full for n in nominations),
        "my_entries": my_entries,
        "form": form or PhotoUploadForm(contest=contest),
        "accept": "image/jpeg,image/png,image/webp",
    }


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
        contest = _visible_contest(request, slug)
        return render(
            request, "photo_contest/contest.html", _contest_context(request, contest)
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
            else:
                messages.success(request, "Фото отправлено на модерацию.")
                return redirect("photo_contest", contest.slug)
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
        if not request.user.has_perm("photo_contest.view_photoentry"):
            raise Http404
        entry = get_object_or_404(PhotoEntry, pk=entry_id)
        try:
            file = entry.original.open("rb")
        except FileNotFoundError:
            raise Http404
        return FileResponse(file)
