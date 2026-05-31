import json
from urllib.parse import quote

from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views import View

from apps.race.forms import RaceForm
from website.forms import NewsPostForm
from website.models import NewsPost, Race, Team
from website.models.race import Category, RegStatus
from website.views.views_ import can_edit_race, is_race_admin

# Escape the HTML-significant characters as their \uXXXX forms, exactly like
# Django's ``json_script``. Escaping ``<`` and ``>`` (not just ``</``) also
# defeats payloads such as ``<!--<script>`` that flip the HTML tokenizer into
# script-data-(double-)escaped mode, where the template's own ``</script>`` no
# longer terminates the block.
_JSON_SCRIPT_ESCAPES = {
    ord("<"): "\\u003C",
    ord(">"): "\\u003E",
    ord("&"): "\\u0026",
}


def _safe_json(data):
    """Serialize ``data`` for embedding inside a <script> block.

    ``ensure_ascii=False`` keeps Cyrillic readable; ``<``, ``>`` and ``&`` are
    escaped so user-supplied text can neither terminate nor confuse the parser
    of the surrounding <script> block.
    """
    json_str = json.dumps(data, ensure_ascii=False).translate(_JSON_SCRIPT_ESCAPES)
    return mark_safe(json_str)


def _categories_with_team_count(race):
    """Active categories for ``race`` annotated with their paid-team count."""
    return (
        Category.active_objects.filter(race=race)
        .order_by("order", "id")
        .annotate(
            team_count=Subquery(
                Team.objects.filter(
                    category2=OuterRef("id"),
                    paid_people__gt=0,
                )
                .values("category2")
                .annotate(count=Count("id"))
                .values("count")[:1]
            )
        )
    )


class RacePageView(View):
    @staticmethod
    def build_context(race, user=None):
        categories = list(_categories_with_team_count(race))
        # Bars in the «Команды» panel are scaled to the largest category (like
        # the teams-page breakdown), so the biggest one fills the track.
        category_max_count = max((c.team_count or 0 for c in categories), default=0)
        news_qs = NewsPost.objects.filter(race=race).order_by("-publication_date")
        news_count = news_qs.count()
        news_list = list(news_qs[:10])
        context = {
            "race": race,
            "categories": categories,
            "category_max_count": category_max_count,
            "links": race.links.order_by("-id"),
            "news_list": news_list,
            "news_count": news_count,
            "reg_open": race.reg_status == RegStatus.OPEN,
            "reg_upcoming": race.reg_status == RegStatus.UPCOMING,
            "race_team_count": race.team_count(),
            "race_people_count": race.people_count(),
        }
        if user is not None and is_race_admin(user, race):
            context["post_form"] = NewsPostForm()
        return context

    def get(self, request, race_slug):
        try:
            race = Race.objects.get(slug=race_slug)
        except Race.DoesNotExist:
            raise Http404
        context = self.build_context(race, request.user)
        return render(request, "race/race_page.html", context)


class RaceTeamsView(View):
    """Unified team-list page (all teams / single category / my teams).

    Three URL names point here with a different ``initial`` filter; everything
    else (search, category filtering, sorting) happens client-side from two
    embedded JSON blocks built by :meth:`build_context`.
    """

    initial = None

    @staticmethod
    def build_context(race, user=None):
        is_authenticated = bool(user is not None and user.is_authenticated)
        is_superuser = bool(user is not None and getattr(user, "is_superuser", False))

        categories = list(_categories_with_team_count(race))
        categories_data = []
        for idx, cat in enumerate(categories):
            color_idx = idx % 8
            categories_data.append(
                {
                    "id": cat.id,
                    "label": cat.short_name or cat.name,
                    "name": cat.name,
                    "count": cat.team_count or 0,
                    "colorIdx": color_idx,
                }
            )

        # Base set: paid teams. Authenticated users also see their own teams in
        # this race (even unpaid) so the «Мои» chip is complete; superusers see
        # every team (matches the old all_teams behavior).
        if is_superuser:
            team_filter = Q(category2__race=race)
        elif is_authenticated:
            team_filter = Q(category2__race=race) & (
                Q(paid_people__gt=0) | Q(owner=user)
            )
        else:
            team_filter = Q(category2__race=race, paid_people__gt=0)
        teams = (
            Team.objects.filter(team_filter)
            .select_related("category2", "owner")
            .order_by("category2__order", "start_number", "id")
        )

        teams_data = []
        for team in teams:
            name = team.teamname or (
                f"Без названия {team.id} "
                f"({team.owner.last_name} {team.owner.first_name})"
            )
            parts = ", ".join(
                p
                for p in (
                    team.athlet1,
                    team.athlet2,
                    team.athlet3,
                    team.athlet4,
                    team.athlet5,
                    team.athlet6,
                )
                if p
            )
            if team.paid_people != team.ucount:
                cnt = f"{team.paid_people:g}/{team.ucount}"
            else:
                cnt = f"{team.paid_people:g}"
            mine = is_authenticated and team.owner_id == user.id
            row = {
                "num": team.start_number,
                "name": name,
                "city": team.city,
                "parts": parts,
                "cnt": cnt,
                "catId": team.category2_id,
                "mine": mine,
            }
            if is_superuser or mine:
                row["edit"] = f"/team/{team.id}"
            teams_data.append(row)

        return {
            "race": race,
            "categories": categories,
            "categories_json": _safe_json(categories_data),
            "teams_json": _safe_json(teams_data),
            "reg_open": race.reg_status == RegStatus.OPEN,
            "race_team_count": race.team_count(),
            "race_people_count": race.people_count(),
            "category_count": len(categories),
            "race_date": race.date,
        }

    def get(self, request, race_slug, category_id=None):
        if self.initial == "mine" and not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("login") + f"?next={request.path}")
        try:
            race = Race.objects.get(slug=race_slug)
        except Race.DoesNotExist:
            raise Http404
        if category_id is not None:
            try:
                category_exists = Category.active_objects.filter(
                    race=race, id=category_id
                ).exists()
            except (ValueError, TypeError):
                raise Http404
            if not category_exists:
                raise Http404
            initial_filter = str(category_id)
        else:
            initial_filter = self.initial or "all"
        context = self.build_context(race, request.user)
        context["initial_filter"] = initial_filter
        return render(request, "race/teams.html", context)


class RaceEditView(View):
    """Create (``races/new/``) and edit (``race/<slug>/edit/``) a race.

    Create is superuser-only; edit is gated on :func:`can_edit_race`. Auth
    mirrors ``AddNewsPostView``: anonymous users are bounced to the login page
    with a ``?next=``, authorized-but-forbidden users get a 403.
    """

    def _load_and_authorize(self, request, race_slug):
        """Resolve the race and check access for both GET and POST.

        Returns ``(race, response)``. ``race`` is ``None`` on the create flow.
        When ``response`` is non-None the caller must return it immediately.
        """
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        if race_slug is None:
            if not request.user.is_superuser:
                return None, HttpResponseForbidden()
            return None, None
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def _build_context(
        self,
        race,
        form=None,
        categories_data=None,
        price_tiers_data=None,
        category_errors=None,
        price_tier_errors=None,
    ):
        if form is None:
            form = RaceForm(instance=race)
        if categories_data is None:
            categories_data = [] if race is None else self._existing_categories(race)
        if price_tiers_data is None:
            price_tiers_data = [] if race is None else self._existing_price_tiers(race)
        return {
            "race": race,
            "form": form,
            "is_create": race is None,
            "categories_data": _safe_json(categories_data),
            "price_tiers_data": _safe_json(price_tiers_data),
            "reg_status_choices": RegStatus.choices,
            "category_errors": category_errors or {},
            "price_tier_errors": price_tier_errors or {},
        }

    @staticmethod
    def _existing_categories(race):
        return [
            {
                "id": cat.id,
                "code": cat.code,
                "short_name": cat.short_name,
                "name": cat.name,
                "description": cat.description,
                "is_active": cat.is_active,
                "min_people": cat.min_people,
                "max_people": cat.max_people,
            }
            for cat in Category.objects.filter(race=race).order_by("order", "id")
        ]

    @staticmethod
    def _existing_price_tiers(race):
        return [
            {
                "id": tier.id,
                "price": tier.price,
                "active_until": tier.active_until.isoformat(),
            }
            for tier in race.price_tiers.all()
        ]

    def get(self, request, race_slug=None):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response
        return render(request, "race/race_form.html", self._build_context(race))
