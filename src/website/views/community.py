from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.race.permissions import is_team_editing_open
from website.models import NewsPost, PublicationKind, Race, RaceAdmin, Team
from website.models.race import RegStatus


def visible_publications():
    """Released publications, excluding posts attached to unpublished races."""
    return NewsPost.objects.visible()


def _team_display_name(team, user):
    """Fallback name for an unnamed team.

    Unlike ``apps.race.views._team_display_name`` the owner is passed in
    explicitly: every team here belongs to ``user`` by construction, so we do
    not need ``select_related("owner")`` and keep the one-query promise.
    """
    if team.teamname:
        return team.teamname
    return f"Без названия {team.id} ({user.last_name} {user.first_name})"


def unfinished_races(today):
    """Published races that have not finished yet, soonest first.

    Named for the criterion (``date_end >= today``, so a race running right now
    counts) — ``RaceListView`` uses ``future_races`` for the stricter
    ``date > today`` meaning, and the two must not be confused.
    """
    return Race.objects.filter(is_published=True, date_end__gte=today).order_by(
        "date", "pk"
    )


def owned_teams_by_race(user, races):
    """The signed-in user's teams in ``races``, grouped by race.

    One query over all races; grouping happens in Python. Anonymous visitors
    get an empty list without touching the database.

    Teams inside a race are ordered by ``category2__order``, then
    ``start_number``, then ``id``. ``Team.start_number`` is a ``CharField``, so
    that sort is lexicographic ("10" before "9"). This is deliberate: it mirrors
    the race-page panel (``apps.race.views._owned_teams``) exactly, and must not
    be "fixed" to a numeric sort in one panel only.
    """
    if user is None or not user.is_authenticated:
        return []

    teams = (
        Team.objects.filter(category2__race__in=races, owner=user)
        .select_related("category2", "category2__race")
        .order_by(
            "category2__race__date",
            "category2__race_id",
            "category2__order",
            "start_number",
            "id",
        )
    )

    groups = []
    current = None
    for team in teams:
        race = team.category2.race
        if current is None or current["race"].pk != race.pk:
            current = {
                "race": race,
                "teams": [],
                "can_change": is_team_editing_open(user, race),
            }
            groups.append(current)
        can_change = current["can_change"]
        current["teams"].append(
            {
                "id": team.id,
                "name": _team_display_name(team, user),
                "number": team.start_number,
                "category": team.category2.short_name or team.category2.name,
                "city": team.city,
                "participants": team.ucount,
                "url": reverse("edit_team", args=[team.id]),
                "action_label": (
                    "Редактировать команду" if can_change else "Посмотреть команду"
                ),
                "can_change": can_change,
            }
        )
    return groups


def get_featured_race(today):
    return (
        Race.objects.filter(
            is_published=True,
            reg_status__in=(RegStatus.OPEN, RegStatus.UPCOMING),
            date_end__gte=today,
        )
        .order_by("date", "date_end", "pk")
        .first()
    )


class HomeView(View):
    template_name = "website/home.html"
    publication_paginate_by = 9

    def get(self, request):
        today = timezone.localdate()
        featured_race = get_featured_race(today)

        unfinished = unfinished_races(today)

        upcoming_races = unfinished
        if featured_race is not None:
            upcoming_races = upcoming_races.exclude(pk=featured_race.pk)

        page_obj = Paginator(
            visible_publications(), self.publication_paginate_by
        ).get_page(request.GET.get("page"))
        context = {
            "featured_race": featured_race,
            "owned_team_groups": owned_teams_by_race(request.user, unfinished),
            "page_obj": page_obj,
            "publications": page_obj.object_list,
            "upcoming_races": upcoming_races[:3],
        }
        return render(request, self.template_name, context)


class ArticleListView(View):
    template_name = "website/publication_list.html"
    paginate_by = 9

    def get(self, request):
        publications = visible_publications().filter(kind=PublicationKind.ARTICLE)
        page_obj = Paginator(publications, self.paginate_by).get_page(
            request.GET.get("page")
        )
        return render(
            request,
            self.template_name,
            {
                "page_obj": page_obj,
                "publications": page_obj.object_list,
                "section_tab": "articles",
                "featured_race": get_featured_race(timezone.localdate()),
                "catalog_title": "Статьи",
                "catalog_description": (
                    "Практические статьи о подготовке, навигации "
                    "и туристских соревнованиях."
                ),
            },
        )


class PublicationDetailView(View):
    template_name = "website/publication_detail.html"

    def get(self, request, pk):
        publication = visible_publications().filter(pk=pk).first()
        is_preview = publication is None
        if is_preview:
            publication = get_object_or_404(
                NewsPost.objects.select_related("race"), pk=pk
            )
            user = request.user
            if not (
                user.is_authenticated
                and user.is_active
                and (
                    user.has_perm("website.change_newspost")
                    or (
                        publication.race_id
                        and RaceAdmin.objects.filter(
                            race_id=publication.race_id, user=user
                        ).exists()
                    )
                )
            ):
                raise Http404
        response = render(
            request,
            self.template_name,
            {"publication": publication, "is_preview": is_preview},
        )
        if is_preview:
            response["Cache-Control"] = "private, no-store"
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response


class RaceListView(View):
    template_name = "website/race_list.html"

    def get(self, request):
        today = timezone.localdate()
        featured_race = get_featured_race(today)
        races = Race.objects.filter(is_published=True)
        if featured_race is not None:
            races = races.exclude(pk=featured_race.pk)
        context = {
            "featured_race": featured_race,
            "featured_race_is_future": (
                featured_race is not None and featured_race.date > today
            ),
            "current_races": races.filter(
                date__lte=today,
                date_end__gte=today,
            ).order_by("date", "pk"),
            "future_races": races.filter(date__gt=today).order_by("date", "pk"),
            "past_races": races.filter(date_end__lt=today).order_by("-date", "pk"),
        }
        return render(request, self.template_name, context)
