import csv
import datetime
import json
import re
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, OuterRef, ProtectedError, Q, Subquery
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views import View
from django.views.decorators.cache import never_cache

from apps.mobile.models import Mark, TrackPoint
from apps.race.app_data import build_overview, build_team_timeline
from apps.race.finance import csv_rows, extras_catalog, filter_rows, payment_rows
from apps.race.forms import RaceForm
from apps.race.models import Protocol, RaceExtra, RacePromo
from apps.race.permissions import can_edit_race, is_team_editing_open
from apps.race.promo import ERROR_MESSAGES as PROMO_ERRORS
from apps.race.promo import PromoError, occupied_team_ids, resolve_promo
from apps.race.results import build_protocol, freeze_protocol
from website.forms import NewsPostForm
from website.models import Checkpoint, NewsPost, Race, Team
from website.models.checkpoint import CheckpointTag
from website.models.enums import CheckpointColor, CheckpointType
from website.models.race import Category, RacePriceTier, RegStatus
from website.views.views_ import is_race_admin

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


def _team_display_name(team):
    return team.teamname or (
        f"Без названия {team.id} " f"({team.owner.last_name} {team.owner.first_name})"
    )


def _owned_teams(race, user):
    """Compact team data for the signed-in user's race-page callout."""
    if user is None or not user.is_authenticated:
        return []

    can_change = is_team_editing_open(user, race)
    teams = (
        Team.objects.filter(category2__race=race, owner=user)
        .select_related("category2", "owner")
        .order_by("category2__order", "start_number", "id")
    )
    return [
        {
            "id": team.id,
            "name": _team_display_name(team),
            "number": team.start_number,
            "category": team.category2.short_name or team.category2.name,
            "city": team.city,
            "participants": team.ucount,
            "url": reverse("edit_team", args=[team.id]),
            "action_label": (
                "Редактировать команду" if can_change else "Посмотреть команду"
            ),
            "can_change": can_change,
            "needs_payment": team.paid_people < team.ucount,
            "paid_people": team.paid_people,
        }
        for team in teams
    ]


class RacePageView(View):
    @staticmethod
    def build_context(race, user=None):
        categories = list(_categories_with_team_count(race))
        race_remaining = race.remaining_people()
        race_full = race_remaining is not None and race_remaining <= 0
        for cat in categories:
            cat.people = cat.people_count()
            if race_full:
                # Когда исчерпан лимит всей гонки, регистрация невозможна ни в одной
                # категории — форсируем ``remaining = 0`` во всех категориях, чтобы
                # бейдж показал «мест нет» даже там, где у категории свой лимит ещё не
                # выбран (или его нет вовсе). 0 → ветка «мест нет» в шаблоне.
                cat.remaining = 0
            else:
                cat.remaining = (
                    None
                    if not cat.people_limit
                    else max(0, cat.people_limit - cat.people)
                )
        is_admin = user is not None and is_race_admin(user, race)
        if is_admin:
            # ``visible()`` applies public filters as well as ordering, so only
            # its ordering is repeated for the admin queryset.
            # Omitting the race publication filter lets admins see posts while
            # previewing an unpublished race.
            news_qs = (
                NewsPost.objects.filter(race=race)
                .select_related("race")
                .order_by("-publication_date", "-pk")
            )
            # A far-future scheduled post sorts first and can push a real post
            # out of the ten rows retained for the admin feed.
        else:
            news_qs = NewsPost.objects.visible().filter(race=race)
        news_count = news_qs.count()
        news_list = list(news_qs[:10])
        context = {
            "race": race,
            "categories": categories,
            "links": race.links.order_by("-id"),
            "news_list": news_list,
            "news_count": news_count,
            "reg_open": race.reg_status == RegStatus.OPEN,
            "reg_upcoming": race.reg_status == RegStatus.UPCOMING,
            "race_team_count": race.team_count(),
            "race_people_count": race.people_count(),
            "race_remaining": race_remaining,
            "race_full": race_full,
            "race_price": race.current_price,
            "owned_teams": _owned_teams(race, user),
        }
        context["can_manage_posts"] = is_admin
        context["can_edit_race"] = bool(user is not None and can_edit_race(user, race))
        if context["can_edit_race"]:
            context["race_administrators"] = sorted(
                [
                    {
                        "user_id": assignment.user_id,
                        "name": assignment.user.get_full_name()
                        or assignment.user.get_username(),
                        "role": assignment.role,
                        "role_label": assignment.get_role_display(),
                    }
                    for assignment in race.race_admins.select_related("user")
                ],
                key=lambda member: (member["role"], member["name"].casefold()),
            )
        return context

    def get(self, request, race_slug):
        try:
            race = Race.objects.get(slug=race_slug)
        except Race.DoesNotExist:
            raise Http404
        if not race.is_published and not (
            request.user.is_superuser or is_race_admin(request.user, race)
        ):
            raise Http404
        context = self.build_context(race, request.user)
        return render(request, "race/race_page.html", context)


class RacePostEditView(View):
    template_name = "race/post_form.html"

    def _load(self, request, race_slug, post_id=None):
        race = get_object_or_404(Race, slug=race_slug)
        if not request.user.is_authenticated:
            return (
                None,
                None,
                HttpResponseRedirect(
                    reverse("login") + "?next=" + quote(request.path, safe="/:@")
                ),
            )
        if not is_race_admin(request.user, race):
            return race, None, HttpResponseForbidden()
        post = (
            get_object_or_404(NewsPost, pk=post_id, race=race)
            if post_id is not None
            else None
        )
        return race, post, None

    def get(self, request, race_slug, post_id=None):
        race, post, response = self._load(request, race_slug, post_id)
        if response is not None:
            return response
        form = NewsPostForm(instance=post)
        return render(
            request,
            self.template_name,
            {"race": race, "post": post, "form": form, "is_edit": post is not None},
        )

    def post(self, request, race_slug, post_id=None):
        race, post, response = self._load(request, race_slug, post_id)
        if response is not None:
            return response
        form = NewsPostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.race = race
            obj.save()
            return HttpResponseRedirect(obj.get_absolute_url())
        return render(
            request,
            self.template_name,
            {"race": race, "post": post, "form": form, "is_edit": post is not None},
        )


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
        has_team_actions = False
        can_change = is_team_editing_open(user, race)
        for team in teams:
            name = _team_display_name(team)
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
                has_team_actions = True
                row["edit"] = f"/team/{team.id}"
                row["action"] = "Редактировать" if can_change else "Посмотреть"
            teams_data.append(row)

        race_remaining = race.remaining_people()
        race_full = race_remaining is not None and race_remaining <= 0
        return {
            "race": race,
            "categories": categories,
            "categories_json": _safe_json(categories_data),
            "teams_json": _safe_json(teams_data),
            "reg_open": race.reg_status == RegStatus.OPEN,
            "reg_upcoming": race.reg_status == RegStatus.UPCOMING,
            "race_team_count": race.team_count(),
            "race_people_count": race.people_count(),
            "race_remaining": race_remaining,
            "race_full": race_full,
            "category_count": len(categories),
            "race_date": race.date,
            "is_authenticated": is_authenticated,
            "has_team_actions": has_team_actions,
            "can_edit_race": bool(user is not None and can_edit_race(user, race)),
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


def _parse_json_list(raw):
    """Parse a JSON array from ``raw`` text.

    Raises ``ValueError`` on malformed JSON or a non-list top-level value so
    the caller can surface a form-level error and roll back.
    """
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        raise ValueError("Некорректные данные.")
    if not isinstance(data, list):
        raise ValueError("Ожидался список.")
    return data


def _row_id(value):
    """Coerce a row ``id`` to ``int`` or ``None`` (new row / unparseable)."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _positive_int(value):
    """Return ``(int, None)`` for a positive integer, else ``(None, msg)``."""
    try:
        ivalue = int(value)
    except (ValueError, TypeError):
        return None, "Введите целое число."
    if ivalue <= 0:
        return None, "Должно быть больше нуля."
    return ivalue, None


def _people_limit_int(value):
    """Parse a people-limit value (``0``/empty = unlimited).

    Returns ``(int, None)`` for a non-negative integer (empty → ``0``), else
    ``(None, msg)``. Negative values are rejected.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0, None
    if isinstance(value, float) and not value.is_integer():
        return None, "Введите целое число."
    try:
        ivalue = int(value)
    except (ValueError, TypeError):
        return None, "Введите целое число."
    if ivalue < 0:
        return None, "Не может быть отрицательным."
    return ivalue, None


def _validate_category_rows(rows):
    """Validate parsed category rows.

    Returns ``(cleaned, errors)`` where ``cleaned`` is a list aligned with
    ``rows`` (``None`` for invalid rows) and ``errors`` is
    ``{row_index: {field: msg}}``. Duplicate ``code`` within the payload is a
    row-level error on the second occurrence.
    """
    errors = {}
    cleaned = []
    seen_codes = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors[index] = {"__all__": "Некорректная строка."}
            cleaned.append(None)
            continue
        row_errors = {}
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        short_name = str(row.get("short_name") or "").strip()
        description = str(row.get("description") or "").strip()
        if not code:
            row_errors["code"] = "Укажите код."
        elif len(code) > 15:
            row_errors["code"] = "Не длиннее 15 символов."
        elif code in seen_codes:
            row_errors["code"] = "Код повторяется."
        if not name:
            row_errors["name"] = "Укажите название."
        elif len(name) > 50:
            row_errors["name"] = "Не длиннее 50 символов."
        if len(short_name) > 15:
            row_errors["short_name"] = "Не длиннее 15 символов."
        if len(description) > 150:
            row_errors["description"] = "Не длиннее 150 символов."
        min_people, min_err = _positive_int(row.get("min_people"))
        max_people, max_err = _positive_int(row.get("max_people"))
        people_limit, limit_err = _people_limit_int(row.get("people_limit"))
        if min_err:
            row_errors["min_people"] = min_err
        if max_err:
            row_errors["max_people"] = max_err
        if limit_err:
            row_errors["people_limit"] = limit_err
        if not min_err and not max_err and min_people > max_people:
            row_errors["min_people"] = "Минимум больше максимума."
        if code and "code" not in row_errors:
            seen_codes.add(code)
        if row_errors:
            errors[index] = row_errors
            cleaned.append(None)
        else:
            cleaned.append(
                {
                    "id": _row_id(row.get("id")),
                    "code": code,
                    "short_name": short_name,
                    "name": name,
                    "description": description,
                    "is_active": bool(row.get("is_active", True)),
                    "min_people": min_people,
                    "max_people": max_people,
                    "people_limit": people_limit,
                }
            )
    return cleaned, errors


def _validate_price_tier_rows(rows):
    """Validate parsed price-tier rows.

    Returns ``(cleaned, errors)`` like :func:`_validate_category_rows`.
    ``price`` must be a positive int; ``active_until`` a valid ``YYYY-MM-DD``.
    """
    errors = {}
    cleaned = []
    seen_dates = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors[index] = {"__all__": "Некорректная строка."}
            cleaned.append(None)
            continue
        row_errors = {}
        price, price_err = _positive_int(row.get("price"))
        if price_err:
            row_errors["price"] = price_err
        active_until = None
        active_until_raw = str(row.get("active_until") or "").strip()
        if not active_until_raw:
            row_errors["active_until"] = "Укажите дату."
        else:
            try:
                active_until = datetime.date.fromisoformat(active_until_raw)
            except ValueError:
                row_errors["active_until"] = "Некорректная дата (ГГГГ-ММ-ДД)."
            else:
                if active_until in seen_dates:
                    row_errors["active_until"] = "Дата повторяется."
                else:
                    seen_dates.add(active_until)
        if row_errors:
            errors[index] = row_errors
            cleaned.append(None)
        else:
            cleaned.append(
                {
                    "id": _row_id(row.get("id")),
                    "price": price,
                    "active_until": active_until,
                }
            )
    return cleaned, errors


_EXTRA_CODE_RE = re.compile(r"^[a-z_]+$")


def _validate_extra_rows(rows):
    """Validate parsed add-on («Доп-услуги») rows.

    Returns ``(cleaned, errors)`` like :func:`_validate_category_rows`.
    ``code`` must be non-empty, unique within the race and match ``[a-z_]+``;
    ``name`` non-empty; ``price`` and ``free_per_team`` non-negative integers.
    """
    errors = {}
    cleaned = []
    seen_codes = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors[index] = {"__all__": "Некорректная строка."}
            cleaned.append(None)
            continue
        row_errors = {}
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code:
            row_errors["code"] = "Укажите код."
        elif len(code) > 32:
            row_errors["code"] = "Не длиннее 32 символов."
        elif not _EXTRA_CODE_RE.match(code):
            row_errors["code"] = "Только строчные латинские буквы и «_»."
        elif code in seen_codes:
            row_errors["code"] = "Код повторяется."
        if not name:
            row_errors["name"] = "Укажите название."
        elif len(name) > 100:
            row_errors["name"] = "Не длиннее 100 символов."
        price, price_err = _people_limit_int(row.get("price"))
        free_per_team, free_err = _people_limit_int(row.get("free_per_team"))
        if price_err:
            row_errors["price"] = price_err
        if free_err:
            row_errors["free_per_team"] = free_err
        if code and "code" not in row_errors:
            seen_codes.add(code)
        if row_errors:
            errors[index] = row_errors
            cleaned.append(None)
        else:
            cleaned.append(
                {
                    "id": _row_id(row.get("id")),
                    "code": code,
                    "name": name,
                    "price": price,
                    "free_per_team": free_per_team,
                    "is_active": bool(row.get("is_active", True)),
                }
            )
    return cleaned, errors


def _reconcile_extras(race, cleaned):
    """Update/create/delete this race's add-ons from ``cleaned`` rows.

    Matches existing rows by ``id`` (cross-race ids treated as new) or, failing
    that, by ``code`` (so the catalogue's ``unique_together(race, code)`` is
    never violated when the JS omits the id of a reactivated row). ``order`` is
    the array index. Rows missing from the payload are **hard-deleted only when
    unused** — a row referenced by any ``TeamExtra``/``PaymentExtra`` is instead
    softly deactivated (``is_active=False``), the deliberately softer policy than
    :func:`_reconcile_categories`; ``PROTECT`` on the FKs is the backstop.
    """
    existing = {extra.id: extra for extra in race.extras.all()}
    by_code = {extra.code: extra for extra in existing.values()}
    seen = set()
    for index, row in enumerate(cleaned):
        row_id = row["id"]
        instance = existing.get(row_id) if row_id is not None else None
        if instance is None:
            instance = by_code.get(row["code"])
        if instance is None:
            instance = RaceExtra(race=race, code=row["code"])
        if not instance.pk:
            instance.code = row["code"]
        instance.name = row["name"]
        instance.price = row["price"]
        instance.free_per_team = row["free_per_team"]
        instance.is_active = row["is_active"]
        instance.order = index
        instance.save()
        seen.add(instance.id)
    for extra in race.extras.exclude(id__in=seen):
        in_use = (
            extra.team_extras.filter(Q(count__gt=0) | Q(count_paid__gt=0)).exists()
            or extra.payment_extras.exists()
        )
        if in_use:
            if extra.is_active:
                extra.is_active = False
                extra.save(update_fields=["is_active"])
        else:
            try:
                extra.delete()
            except ProtectedError:
                extra.is_active = False
                extra.save(update_fields=["is_active"])


_PROMO_CODE_RE = re.compile(r"^[A-Z0-9_-]{2,32}$")


def _validate_promo_rows(rows):
    """Validate parsed promo-code rows.

    Returns ``(cleaned, errors)`` like :func:`_validate_category_rows`. ``code``
    is upper-cased, must match ``[A-Z0-9_-]{2,32}`` and be unique within the
    race; ``value`` is 1..100 for a percent and > 0 for a fixed discount;
    ``max_uses`` is a non-negative integer (0 = unlimited).
    """
    errors = {}
    cleaned = []
    seen_codes = set()
    valid_types = {RacePromo.PERCENT, RacePromo.FIXED}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors[index] = {"__all__": "Некорректная строка."}
            cleaned.append(None)
            continue
        row_errors = {}
        code = str(row.get("code") or "").strip().upper()
        if not code:
            row_errors["code"] = "Укажите код."
        elif not _PROMO_CODE_RE.match(code):
            row_errors["code"] = "2–32 символа: A–Z, 0–9, «-», «_»."
        elif code in seen_codes:
            row_errors["code"] = "Код повторяется."

        discount_type = str(row.get("discount_type") or "").strip()
        if discount_type not in valid_types:
            row_errors["discount_type"] = "Выберите тип скидки."

        value, value_err = _people_limit_int(row.get("value"))
        if value_err:
            row_errors["value"] = value_err
        elif discount_type == RacePromo.PERCENT and not (1 <= value <= 100):
            row_errors["value"] = "Процент от 1 до 100."
        elif discount_type == RacePromo.FIXED and value < 1:
            row_errors["value"] = "Сумма должна быть больше 0."

        max_uses, max_uses_err = _people_limit_int(row.get("max_uses"))
        if max_uses_err:
            row_errors["max_uses"] = max_uses_err

        comment = str(row.get("comment") or "").strip()
        if len(comment) > 255:
            row_errors["comment"] = "Не длиннее 255 символов."

        if code and "code" not in row_errors:
            seen_codes.add(code)
        if row_errors:
            errors[index] = row_errors
            cleaned.append(None)
        else:
            cleaned.append(
                {
                    "id": _row_id(row.get("id")),
                    "code": code,
                    "discount_type": discount_type,
                    "value": value,
                    "max_uses": max_uses,
                    "comment": comment,
                    "is_active": bool(row.get("is_active", True)),
                }
            )
    return cleaned, errors


def _reconcile_promos(race, cleaned):
    """Update/create/delete this race's promo codes from ``cleaned`` rows.

    Matches existing rows by ``id`` (cross-race ids treated as new) or by
    ``code``. ``code`` is **read-only once saved**: an edit to an existing row
    keeps the stored code, so payments already referencing it keep their meaning.
    Rows missing from the payload are hard-deleted only when unused — a promo
    referenced by any ``Payment`` is softly deactivated instead (same policy as
    :func:`_reconcile_extras`; ``PROTECT`` on the FK is the backstop).
    """
    existing = {promo.id: promo for promo in race.promos.all()}
    by_code = {promo.code: promo for promo in existing.values()}
    seen = set()
    for index, row in enumerate(cleaned):
        row_id = row["id"]
        instance = existing.get(row_id) if row_id is not None else None
        if instance is None:
            instance = by_code.get(row["code"])
        if instance is None:
            instance = RacePromo(race=race, code=row["code"])
        if not instance.pk:
            instance.code = row["code"]
        instance.discount_type = row["discount_type"]
        instance.value = row["value"]
        instance.max_uses = row["max_uses"]
        instance.comment = row["comment"]
        instance.is_active = row["is_active"]
        instance.order = index
        instance.save()
        seen.add(instance.id)
    for promo in race.promos.exclude(id__in=seen):
        if promo.payments.exists():
            if promo.is_active:
                promo.is_active = False
                promo.save(update_fields=["is_active"])
        else:
            try:
                promo.delete()
            except ProtectedError:
                promo.is_active = False
                promo.save(update_fields=["is_active"])


def _reconcile_categories(race, cleaned):
    """Update/create/delete this race's categories from ``cleaned`` rows.

    A row ``id`` not belonging to ``race`` is treated as a new row (never an
    update or delete of another race's category). ``order`` is the array index.
    """
    existing = {cat.id: cat for cat in Category.objects.filter(race=race)}
    seen = set()
    for index, row in enumerate(cleaned):
        row_id = row["id"]
        instance = existing.get(row_id) if row_id is not None else None
        if instance is None:
            instance = Category(race=race)
        instance.code = row["code"]
        instance.short_name = row["short_name"]
        instance.name = row["name"]
        instance.description = row["description"]
        instance.is_active = row["is_active"]
        instance.min_people = row["min_people"]
        instance.max_people = row["max_people"]
        instance.people_limit = row["people_limit"]
        instance.order = index
        instance.save()
        seen.add(instance.id)
    to_delete_ids = list(
        Category.objects.filter(race=race)
        .exclude(id__in=seen)
        .select_for_update()
        .values_list("id", flat=True)
    )
    if Team.objects.filter(category2__in=to_delete_ids).exists():
        names = ", ".join(
            f"«{c.name}»"
            for c in Category.objects.filter(id__in=to_delete_ids).only("name")
        )
        raise ValueError(f"Нельзя удалить категорию, в которой есть команды: {names}.")
    Category.objects.filter(id__in=to_delete_ids).delete()


def _reconcile_price_tiers(race, cleaned):
    """Update/create/delete this race's price tiers from ``cleaned`` rows.

    Cross-race ``id`` guard and ``order = index`` as in
    :func:`_reconcile_categories`.
    """
    existing = {tier.id: tier for tier in race.price_tiers.all()}
    seen = set()
    for index, row in enumerate(cleaned):
        row_id = row["id"]
        instance = existing.get(row_id) if row_id is not None else None
        if instance is None:
            instance = RacePriceTier(race=race)
        instance.price = row["price"]
        instance.active_until = row["active_until"]
        instance.order = index
        instance.save()
        seen.add(instance.id)
    race.price_tiers.exclude(id__in=seen).delete()


_CHECKPOINT_TYPE_VALUES = {value for value, _ in CheckpointType.choices}
_CHECKPOINT_COLOR_VALUES = {value for value, _ in CheckpointColor.choices}


def _validate_legend_rows(rows):
    """Validate parsed legend (checkpoint) rows.

    Returns ``(cleaned, errors)`` like :func:`_validate_category_rows`.
    ``number``/``cost`` are non-negative integers (empty → ``0``); ``type`` must
    be a valid :class:`CheckpointType` (empty → ``kp``); ``color`` must be a valid
    :class:`CheckpointColor` (empty → ``""``); ``description`` is trimmed and
    capped at the model's 200-char limit; ``is_legend_locked`` is a bool.
    """
    errors = {}
    cleaned = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors[index] = {"__all__": "Некорректная строка."}
            cleaned.append(None)
            continue
        row_errors = {}
        number, number_err = _people_limit_int(row.get("number"))
        cost, cost_err = _people_limit_int(row.get("cost"))
        if number_err:
            row_errors["number"] = number_err
        if cost_err:
            row_errors["cost"] = cost_err
        type_ = str(row.get("type") or "").strip() or CheckpointType.kp.value
        if type_ not in _CHECKPOINT_TYPE_VALUES:
            row_errors["type"] = "Неизвестный тип точки."
        color = str(row.get("color") or "").strip()
        if color not in _CHECKPOINT_COLOR_VALUES:
            row_errors["color"] = "Неизвестный цвет."
        description = str(row.get("description") or "").strip()
        if len(description) > 200:
            row_errors["description"] = "Не длиннее 200 символов."
        if row_errors:
            errors[index] = row_errors
            cleaned.append(None)
        else:
            cleaned.append(
                {
                    "id": _row_id(row.get("id")),
                    "number": number,
                    "cost": cost,
                    "description": description,
                    "type": type_,
                    "color": color,
                    "is_legend_locked": bool(row.get("is_legend_locked")),
                }
            )
    return cleaned, errors


def _reconcile_legend(race, cleaned):
    """Update/create/delete this race's checkpoints from ``cleaned`` rows.

    A row ``id`` not belonging to ``race`` is treated as a new row (never an
    update or delete of another race's КП). Each КП is saved **one at a time via
    ``instance.save()``** — never ``QuerySet.update()`` — so the legend-crypto
    ``post_save`` signals fire: toggling ``is_legend_locked`` is exactly what
    creates/destroys the ``CheckpointSecret`` and rebuilds dependent tag bundles,
    and a bulk update would skip that and leak the cleartext legend.

    Deletion guard: a КП missing from the payload is deleted only when it has no
    NFC ``CheckpointTag`` rows — those are physically provisioned chips and a
    ``CASCADE`` delete would silently destroy them, so such a row raises a
    ``ValueError`` that rolls the whole save back. (``TakenKP`` scan history is
    keyed by ``point_number``, not an FK, so it neither blocks nor cascades.)
    """
    existing = {cp.id: cp for cp in Checkpoint.objects.filter(race=race)}
    seen = set()
    for row in cleaned:
        row_id = row["id"]
        instance = existing.get(row_id) if row_id is not None else None
        if instance is None:
            instance = Checkpoint(race=race)
        instance.number = row["number"]
        instance.cost = row["cost"]
        instance.description = row["description"]
        instance.type = row["type"]
        instance.color = row["color"]
        instance.is_legend_locked = row["is_legend_locked"]
        instance.save()
        seen.add(instance.id)
    for cp in (
        Checkpoint.objects.filter(race=race).exclude(id__in=seen).select_for_update()
    ):
        if cp.tags.exists():
            raise ValueError(
                f"КП «{cp.number}» нельзя удалить: к нему привязаны NFC-теги. "
                "Сначала удалите теги."
            )
        cp.delete()


class RaceEditView(View):
    """Create (``races/new/``) and edit (``race/<slug>/edit/``) a race.

    Create is superuser-only; edit is gated on :func:`can_edit_race`. Auth
    mirrors ``RacePostEditView``: anonymous users are bounced to the login page
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
        extras_data=None,
        promos_data=None,
        category_errors=None,
        price_tier_errors=None,
        extra_errors=None,
        promo_errors=None,
    ):
        if form is None:
            form = RaceForm(instance=race)
        if categories_data is None:
            categories_data = [] if race is None else self._existing_categories(race)
        if price_tiers_data is None:
            price_tiers_data = [] if race is None else self._existing_price_tiers(race)
        if extras_data is None:
            extras_data = [] if race is None else self._existing_extras(race)
        if promos_data is None:
            promos_data = [] if race is None else self._existing_promos(race)
        return {
            "race": race,
            "form": form,
            "is_create": race is None,
            "categories_data": _safe_json(categories_data),
            "price_tiers_data": _safe_json(price_tiers_data),
            "extras_data": _safe_json(extras_data),
            "promos_data": _safe_json(promos_data),
            "category_errors": _safe_json(category_errors or {}),
            "price_tier_errors": _safe_json(price_tier_errors or {}),
            "extra_errors": _safe_json(extra_errors or {}),
            "promo_errors": _safe_json(promo_errors or {}),
            "promo_type_choices": RacePromo.DISCOUNT_TYPE_CHOICES,
            "reg_status_choices": RegStatus.choices,
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
                "people_limit": cat.people_limit,
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
            for tier in race.price_tiers.order_by("order", "id")
        ]

    @staticmethod
    def _existing_extras(race):
        # ``has_teams`` lets the JS show «remove → deactivate» (the row is in
        # use and ``PROTECT``-guarded) instead of a hard delete.
        return [
            {
                "id": extra.id,
                "code": extra.code,
                "name": extra.name,
                "price": extra.price,
                "free_per_team": extra.free_per_team,
                "is_active": extra.is_active,
                "has_teams": (
                    extra.team_extras.exists() or extra.payment_extras.exists()
                ),
            }
            for extra in race.extras.order_by("order", "id")
        ]

    @staticmethod
    def _existing_promos(race):
        # ``used`` is derived from Payment rows (apps/race/promo.py), so it is
        # one small query per code — a race has a handful of them at most.
        # ``has_payments`` drives the JS «remove → deactivate» switch.
        return [
            {
                "id": promo.id,
                "code": promo.code,
                "discount_type": promo.discount_type,
                "value": promo.value,
                "max_uses": promo.max_uses,
                "comment": promo.comment,
                "is_active": promo.is_active,
                "used": len(occupied_team_ids(promo)),
                "has_payments": promo.payments.exists(),
            }
            for promo in race.promos.order_by("order", "id")
        ]

    def get(self, request, race_slug=None):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response
        return render(request, "race/race_form.html", self._build_context(race))

    def post(self, request, race_slug=None):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response
        is_create = race is None

        form = RaceForm(request.POST, instance=race)
        form_valid = form.is_valid()

        category_rows = price_tier_rows = None
        try:
            raw_cats = request.POST.get("categories_json")
            if not raw_cats:
                raise ValueError("Данные не переданы.")
            category_rows = _parse_json_list(raw_cats)
        except ValueError as exc:
            form.add_error(None, f"Категории: {exc}")
        try:
            raw_tiers = request.POST.get("price_tiers_json")
            if not raw_tiers:
                raise ValueError("Данные не переданы.")
            price_tier_rows = _parse_json_list(raw_tiers)
        except ValueError as exc:
            form.add_error(None, f"Ценовые периоды: {exc}")

        # Add-ons are optional: a missing/empty payload means «no add-ons» (not
        # an error like categories/tiers), so a race can have none.
        extra_rows = None
        try:
            extra_rows = _parse_json_list(request.POST.get("extras_json") or "[]")
        except ValueError as exc:
            form.add_error(None, f"Доп-услуги: {exc}")

        # Promo codes are optional too — an empty payload means «no codes».
        promo_rows = None
        try:
            promo_rows = _parse_json_list(request.POST.get("promos_json") or "[]")
        except ValueError as exc:
            form.add_error(None, f"Промокоды: {exc}")

        category_errors = {}
        price_tier_errors = {}
        extra_errors = {}
        promo_errors = {}
        cleaned_categories = cleaned_tiers = cleaned_extras = cleaned_promos = None
        if category_rows is not None:
            cleaned_categories, category_errors = _validate_category_rows(category_rows)
        if price_tier_rows is not None:
            cleaned_tiers, price_tier_errors = _validate_price_tier_rows(
                price_tier_rows
            )
        if extra_rows is not None:
            cleaned_extras, extra_errors = _validate_extra_rows(extra_rows)
        if promo_rows is not None:
            cleaned_promos, promo_errors = _validate_promo_rows(promo_rows)

        if category_errors:
            bad = ", ".join(str(i + 1) for i in sorted(category_errors))
            form.add_error(None, f"Ошибки в категориях (строки: {bad}).")
        if price_tier_errors:
            bad = ", ".join(str(i + 1) for i in sorted(price_tier_errors))
            form.add_error(None, f"Ошибки в ценовых периодах (строки: {bad}).")
        if extra_errors:
            bad = ", ".join(str(i + 1) for i in sorted(extra_errors))
            form.add_error(None, f"Ошибки в доп-услугах (строки: {bad}).")
        if promo_errors:
            bad = ", ".join(str(i + 1) for i in sorted(promo_errors))
            form.add_error(None, f"Ошибки в промокодах (строки: {bad}).")

        if (
            form_valid
            and category_rows is not None
            and price_tier_rows is not None
            and extra_rows is not None
            and promo_rows is not None
            and not category_errors
            and not price_tier_errors
            and not extra_errors
            and not promo_errors
        ):
            try:
                with transaction.atomic():
                    race = form.save()
                    _reconcile_categories(race, cleaned_categories)
                    _reconcile_price_tiers(race, cleaned_tiers)
                    _reconcile_extras(race, cleaned_extras)
                    _reconcile_promos(race, cleaned_promos)
            except ValueError as exc:
                if not is_create:
                    race.refresh_from_db()
                form.add_error(None, str(exc))
            else:
                return HttpResponseRedirect(
                    reverse("race", kwargs={"race_slug": race.slug})
                )

        # On any error: re-render echoing the submitted payloads (so unsaved
        # rows survive) plus per-row errors and the bound form's field errors.
        # Use the original pre-save race (None on create) so is_create stays correct
        # even if form.save() ran and was rolled back inside the atomic block.
        render_race = None if is_create else race

        # Re-attach has_teams from the DB so the JS still shows «deactivate»
        # (not «delete») for extras that are already referenced by teams.
        if render_race is not None and extra_rows:
            existing_map = {
                e["id"]: e["has_teams"] for e in self._existing_extras(render_race)
            }
            for row in extra_rows:
                if row.get("id") in existing_map:
                    row["has_teams"] = existing_map[row["id"]]

        # Same for promo codes: keep «used»/«deactivate» accurate on re-render.
        if render_race is not None and promo_rows:
            promo_map = {
                p["id"]: p for p in self._existing_promos(render_race) if p["id"]
            }
            for row in promo_rows:
                stored = promo_map.get(row.get("id"))
                if stored:
                    row["used"] = stored["used"]
                    row["has_payments"] = stored["has_payments"]
                    row["code"] = stored["code"]

        context = self._build_context(
            render_race,
            form=form,
            categories_data=category_rows or [],
            price_tiers_data=price_tier_rows or [],
            extras_data=extra_rows or [],
            promos_data=promo_rows or [],
            category_errors=category_errors,
            price_tier_errors=price_tier_errors,
            extra_errors=extra_errors,
            promo_errors=promo_errors,
        )
        return render(request, "race/race_form.html", context)


class RaceLegendEditView(View):
    """Bulk-edit a race's legend (checkpoints) on a dedicated page.

    Mirrors :class:`RaceEditView`'s JSON-island + reconcile pattern, but for a
    single entity (``Checkpoint``): the page renders one spreadsheet-like grid
    seeded from ``#checkpoints-data`` and posts the rows back in the hidden
    ``checkpoints_json`` input. The grid accepts paste straight from
    Excel/Google Sheets (TSV). Gated on :func:`can_edit_race`.
    """

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    @staticmethod
    def _existing_checkpoints(race):
        # ``has_tags`` lets the JS block the «delete» of a КП with provisioned
        # NFC tags (mirrors the server-side guard in :func:`_reconcile_legend`).
        return [
            {
                "id": cp.id,
                "number": cp.number,
                "type": cp.type,
                "color": cp.color,
                "cost": cp.cost,
                "description": cp.description,
                "is_legend_locked": cp.is_legend_locked,
                "has_tags": bool(cp.tags.all()),
            }
            for cp in Checkpoint.objects.filter(race=race)
            .prefetch_related("tags")
            .order_by("number", "id")
        ]

    def _build_context(
        self, race, checkpoints_data=None, checkpoint_errors=None, form_errors=None
    ):
        if checkpoints_data is None:
            checkpoints_data = self._existing_checkpoints(race)
        return {
            "race": race,
            "checkpoints_data": _safe_json(checkpoints_data),
            "checkpoint_errors": _safe_json(checkpoint_errors or {}),
            "legend_config": _safe_json(
                {
                    "types": [
                        {"value": value, "label": label}
                        for value, label in CheckpointType.choices
                    ],
                    "colors": [
                        {"value": value, "label": label}
                        for value, label in CheckpointColor.choices
                    ],
                    "descMaxLen": 200,
                }
            ),
            "form_errors": form_errors or [],
        }

    def get(self, request, race_slug):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response
        return render(request, "race/legend_form.html", self._build_context(race))

    def post(self, request, race_slug):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response

        form_errors = []
        rows = None
        try:
            rows = _parse_json_list(request.POST.get("checkpoints_json") or "[]")
        except ValueError as exc:
            form_errors.append(f"Контрольные точки: {exc}")

        checkpoint_errors = {}
        cleaned = None
        if rows is not None:
            cleaned, checkpoint_errors = _validate_legend_rows(rows)
        if checkpoint_errors:
            bad = ", ".join(str(i + 1) for i in sorted(checkpoint_errors))
            form_errors.append(f"Ошибки в строках: {bad}.")

        if rows is not None and not checkpoint_errors:
            try:
                with transaction.atomic():
                    _reconcile_legend(race, cleaned)
            except ValueError as exc:
                form_errors.append(str(exc))
            else:
                return HttpResponseRedirect(
                    reverse("edit_legend", kwargs={"race_slug": race.slug})
                )

        # Re-render echoing the submitted rows + per-row errors. Re-attach
        # ``has_tags`` from the DB so the JS still blocks deleting a tagged КП.
        if rows:
            tagged = {
                cp["id"]: cp["has_tags"] for cp in self._existing_checkpoints(race)
            }
            for row in rows:
                if isinstance(row, dict) and row.get("id") in tagged:
                    row["has_tags"] = tagged[row["id"]]

        context = self._build_context(
            race,
            checkpoints_data=rows or [],
            checkpoint_errors=checkpoint_errors,
            form_errors=form_errors,
        )
        return render(request, "race/legend_form.html", context)


@method_decorator(never_cache, name="dispatch")
class RaceLegendCodesView(View):
    """Read-only view of a race's per-tag NFC codes (legend provisioning).

    The web twin of ``manage.py export_legend_codes --race <id>``: one row per
    :class:`CheckpointTag` of the race — ``nfc_uid / КП number / code(hex)`` —
    so the field crew can read each tag's ``code`` (written into the chip's NFC
    user memory) without shell access. Same queryset, ordering and ``—``
    placeholder (tags without a code yet) as the command. Gated on
    :func:`can_edit_race`; codes are admin-only, consistent with the cleartext
    legend already shown to admins on the edit page.
    """

    def get(self, request, race_slug):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return HttpResponseForbidden()
        tags = (
            CheckpointTag.objects.filter(checkpoint__race_id=race.id)
            .select_related("checkpoint")
            .order_by("checkpoint__number", "id")
        )
        rows = [
            {
                "nfc_uid": tag.nfc_uid,
                "number": tag.checkpoint.number,
                "code": bytes(tag.code).hex() if tag.code else "—",
            }
            for tag in tags
        ]
        return render(request, "race/legend_codes.html", {"race": race, "rows": rows})


class ProtocolView(View):
    """Read-only results-protocol page, backed by the ``ProtocolRow`` snapshot.

    Visibility: :func:`can_edit_race` sees the latest protocol of any status
    (draft or final); everyone else sees only the latest ``final`` one. The
    page never touches live ``Team``/``TakenKP`` data — only whatever a past
    :func:`apps.race.results.build_protocol` call snapshotted into rows.
    """

    def get(self, request, race_slug, category_id):
        race = get_object_or_404(Race, slug=race_slug)
        can_edit = can_edit_race(request.user, race)
        category = Category.objects.filter(id=category_id, race=race).first()

        protocol_qs = race.protocols.all()
        if not can_edit:
            protocol_qs = protocol_qs.filter(status=Protocol.FINAL)
        protocol = protocol_qs.order_by("-created_at").first()

        if protocol is None:
            return render(
                request,
                "race/protocol.html",
                {
                    "race": race,
                    "category": category,
                    "can_edit": can_edit,
                    "protocol": None,
                    "rows": [],
                },
            )

        rows = protocol.rows.filter(category_id=category_id).order_by("place")
        title = (
            "Предварительный протокол"
            if protocol.status == Protocol.DRAFT
            else "Итоговый протокол"
        )
        return render(
            request,
            "race/protocol.html",
            {
                "race": race,
                "category": category,
                "can_edit": can_edit,
                "protocol": protocol,
                "rows": rows,
                "title": title,
            },
        )


def _protocol_redirect_back(request, race):
    """Redirect back to where a build/freeze POST came from.

    Prefers ``HTTP_REFERER`` (validated against ``url_has_allowed_host_and_scheme``
    to rule out an off-site redirect via a spoofed header); falls back to the
    results page of the race's first active category, then to the race page
    itself if the race has none.
    """
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return HttpResponseRedirect(referer)
    category = Category.active_objects.filter(race=race).order_by("order", "id").first()
    if category is not None:
        return HttpResponseRedirect(
            reverse(
                "category_results",
                kwargs={"race_slug": race.slug, "category_id": category.id},
            )
        )
    return HttpResponseRedirect(reverse("race", kwargs={"race_slug": race.slug}))


class ProtocolBuildView(View):
    """Recompute the race's draft protocol from live data. Admin-only POST."""

    def post(self, request, race_slug):
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return HttpResponseForbidden()
        build_protocol(race, request.user)
        messages.success(request, "Протокол сформирован (черновик).")
        return _protocol_redirect_back(request, race)


class ProtocolFreezeView(View):
    """Freeze the race's latest draft protocol into an immutable final.

    Admin-only POST.
    """

    def post(self, request, race_slug):
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return HttpResponseForbidden()
        protocol = freeze_protocol(race)
        if protocol is None:
            messages.info(request, "Нет черновика для фиксации.")
        else:
            messages.success(request, "Протокол зафиксирован.")
        return _protocol_redirect_back(request, race)


class RaceMapView(View):
    """Organizer-only «Карта гонки» page: markers + on-demand tracks.

    Gated on :func:`can_edit_race` like :class:`RaceLegendEditView`. The
    heavy lifting (positions polling, track fetch/draw) lives entirely in
    ``race_map.js``, driven by the ``#raceMapConfig`` JSON island — this view
    only resolves the two endpoint URLs. ``trackUrlTemplate`` is built via
    ``reverse()`` with a placeholder ``team_id`` and a string substitution
    (``reverse()`` itself can't leave a template placeholder in the path).
    """

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def get(self, request, race_slug):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response

        positions_url = reverse("race_map_positions", kwargs={"race_slug": race.slug})
        track_url_placeholder = reverse(
            "race_map_track", kwargs={"race_slug": race.slug, "team_id": 0}
        )
        track_url_template = re.sub(r"/0/$", "/{team_id}/", track_url_placeholder)
        marks_url = reverse("race_map_marks", kwargs={"race_slug": race.slug})

        context = {
            "race": race,
            "map_config": _safe_json(
                {
                    "positionsUrl": positions_url,
                    "trackUrlTemplate": track_url_template,
                    "marksUrl": marks_url,
                    "tileUrls": {
                        "osm": settings.MAP_TILE_URL_OSM,
                        "topo": settings.MAP_TILE_URL_TOPO,
                    },
                }
            ),
        }
        return render(request, "race/map.html", context)


class RaceMapPositionsView(View):
    """Last known GPS position per team, for the organizer race-map page.

    Gated on :func:`can_edit_race` like :class:`RaceLegendEditView`. Returns
    **all** teams of the race; a team with no ``TrackPoint`` rows gets
    ``lat``/``lon``/``gps_time_ms``/``received_at``/``install_id``/
    ``segment_id`` all ``null`` (the JS sidebar groups those as «не шлют
    трек»).
    """

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def get(self, request, race_slug):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response

        # ``-created_at``/``-id`` are deterministic tie-breakers: two phones
        # of one team can upload different points with the same
        # ``gps_time_ms``, and a bare DISTINCT ON would pick either row per
        # request (marker flicker).
        last_points = {
            point["team_id"]: point
            for point in TrackPoint.objects.filter(race_id=race.id)
            .order_by("team_id", "-gps_time_ms", "-created_at", "-id")
            .distinct("team_id")
            .values(
                "team_id",
                "lat",
                "lon",
                "gps_time_ms",
                "created_at",
                "install_id",
                "segment_id",
            )
        }

        teams = Team.objects.filter(category2__race_id=race.id)
        rows = []
        for team in teams:
            point = last_points.get(team.id)
            rows.append(
                {
                    "team_id": team.id,
                    "name": team.teamname,
                    "number": team.start_number,
                    "lat": point["lat"] if point else None,
                    "lon": point["lon"] if point else None,
                    "gps_time_ms": point["gps_time_ms"] if point else None,
                    "received_at": (point["created_at"].isoformat() if point else None),
                    "install_id": point["install_id"] if point else None,
                    "segment_id": point["segment_id"] if point else None,
                }
            )
        return JsonResponse(rows, safe=False)


class RaceMapTrackView(View):
    """One team's thinned GPS track, split into per-session polylines.

    A "session" is the pair ``(install_id, segment_id)`` — per the
    ``TrackPoint`` model doc, two phones of one team recording at once must
    not merge into one line. Within a session a point is kept only if
    ``THIN_INTERVAL_MS`` has passed since the previously kept point; a
    session's last point is always kept so the line reaches its true end.
    """

    THIN_INTERVAL_MS = 30_000

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def _thin_session(self, points):
        kept = []
        last_kept = None
        for point in points:
            gps_time_ms = point[2]
            if last_kept is None or gps_time_ms - last_kept[2] >= self.THIN_INTERVAL_MS:
                kept.append(point)
                last_kept = point
        last_point = points[-1]
        if not kept or kept[-1] != last_point:
            kept.append(last_point)
        return [[lat, lon] for lat, lon, _ in kept]

    def get(self, request, race_slug, team_id):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response

        team = get_object_or_404(Team, pk=team_id, category2__race_id=race.id)

        points = (
            TrackPoint.objects.filter(race_id=race.id, team_id=team.id)
            .order_by("gps_time_ms", "created_at", "id")
            .values_list("install_id", "segment_id", "lat", "lon", "gps_time_ms")
        )

        sessions = {}
        session_order = []
        for install_id, segment_id, lat, lon, gps_time_ms in points:
            key = (install_id, segment_id)
            if key not in sessions:
                sessions[key] = []
                session_order.append(key)
            sessions[key].append((lat, lon, gps_time_ms))

        segments = [
            {
                "install_id": key[0],
                "segment_id": key[1],
                "points": self._thin_session(sessions[key]),
            }
            for key in session_order
        ]
        return JsonResponse({"segments": segments})


class RaceMapMarksView(View):
    """All located checkpoint takes of a race, for the «Взятия КП» map layer.

    Gated on :func:`can_edit_race` like the other map views. ``Checkpoint``
    has no coordinates in the DB, so the cluster of take points per КП is the
    only ground truth of where a КП actually stands — takes without a GPS fix
    are filtered out server-side. ``Mark.checkpoint_id`` is a plain int (not
    an FK): an unknown id yields ``cp_number: null`` (rendered as «КП?» —
    exactly the rows worth analyzing). Fetched once per page load, no
    pagination/thinning (thousands of rows at most).
    """

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def get(self, request, race_slug):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response

        cp_numbers = dict(
            Checkpoint.objects.filter(race=race).values_list("id", "number")
        )

        marks = (
            Mark.objects.filter(
                race_id=race.id, loc_lat__isnull=False, loc_lon__isnull=False
            )
            .order_by("created_at", "id")
            .values(
                "id",
                "team_id",
                "team__teamname",
                "team__start_number",
                "checkpoint_id",
                "loc_lat",
                "loc_lon",
                "loc_accuracy",
                "verified",
                "method",
                "trusted_ms",
                "wall_ms",
            )
        )

        rows = [
            {
                "mark_id": mark["id"],
                "team_id": mark["team_id"],
                "team_name": mark["team__teamname"],
                "team_number": mark["team__start_number"],
                "checkpoint_id": mark["checkpoint_id"],
                "cp_number": cp_numbers.get(mark["checkpoint_id"]),
                "lat": mark["loc_lat"],
                "lon": mark["loc_lon"],
                "accuracy": mark["loc_accuracy"],
                "verified": mark["verified"],
                "method": mark["method"],
                "time_ms": (
                    mark["trusted_ms"]
                    if mark["trusted_ms"] is not None
                    else mark["wall_ms"]
                ),
            }
            for mark in marks
        ]
        return JsonResponse(rows, safe=False)


class RaceAppDataView(View):
    """Organizer-only «Данные приложения» overview: one row per team.

    A visual cross-check of everything the mobile app uploaded for a race —
    stored boundary times vs the verified boundary takes they were derived
    from, the team's chips (via ``MarkPresent``), judge start/finish scans
    attributed by chip uid, and upload activity counters. Gated on
    :func:`can_edit_race` like :class:`RaceMapView`; all aggregation lives in
    :mod:`apps.race.app_data`.
    """

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def get(self, request, race_slug):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response
        context = build_overview(race)
        context["race"] = race
        return render(request, "race/app_data.html", context)


class RaceAppDataTeamView(View):
    """One team's chronological feed of app events (marks, scans, track).

    Same gate as :class:`RaceAppDataView`; 404 when the team is not in the
    race (mirrors :class:`RaceMapTrackView`).
    """

    def _load_and_authorize(self, request, race_slug):
        if not request.user.is_authenticated:
            return None, HttpResponseRedirect(
                reverse("login") + "?next=" + quote(request.path, safe="/:@")
            )
        race = get_object_or_404(Race, slug=race_slug)
        if not can_edit_race(request.user, race):
            return race, HttpResponseForbidden()
        return race, None

    def get(self, request, race_slug, team_id):
        race, response = self._load_and_authorize(request, race_slug)
        if response is not None:
            return response
        team = get_object_or_404(Team, pk=team_id, category2__race_id=race.id)
        context = build_team_timeline(race, team)
        context["race"] = race
        context["team"] = team
        return render(request, "race/app_data_team.html", context)


class PromoCheckView(View):
    """Validate a promo code for the team form and answer in JSON.

    **GET, not POST**: the check writes nothing, and no JS in this project posts
    — there is no CSRF-token helper to reuse — so a GET both avoids that and
    follows the JSON-GET precedent of the race-map endpoints. The response
    carries the discount *rule* (``type``/``value``), not a total: the JS
    recomputes the sum as the team size changes.
    """

    def get(self, request, race_slug):
        if not request.user.is_authenticated:
            # JSON, not a redirect to the HTML login page — the caller is AJAX.
            return JsonResponse({"ok": False, "error": "Войдите в аккаунт"}, status=403)
        race = get_object_or_404(Race, slug=race_slug, is_published=True)
        code = (request.GET.get("code") or "").strip()
        if not code:
            return JsonResponse({"ok": False, "error": PROMO_ERRORS["not_found"]})
        try:
            promo = resolve_promo(race, code, self._resolve_team(request, race))
        except PromoError as exc:
            return JsonResponse({"ok": False, "error": str(exc)})
        return JsonResponse(
            {
                "ok": True,
                "code": promo.code,
                "type": promo.discount_type,
                "value": promo.value,
            }
        )

    @staticmethod
    def _resolve_team(request, race):
        """The team being edited, or ``None`` (the add flow).

        A ``team_id`` outside this race, or one the user neither owns nor
        administers, is ignored rather than rejected — the code is then checked
        as it would be for a brand-new team.
        """
        raw = request.GET.get("team_id")
        if not raw:
            return None
        try:
            team_id = int(raw)
        except (TypeError, ValueError):
            return None
        team = Team.objects.filter(id=team_id, category2__race=race).first()
        if team is None:
            return None
        if team.owner_id != request.user.id and not can_edit_race(request.user, race):
            return None
        return team


def _load_race_for_admin(request, race_slug):
    """``(race, error_response)`` for the race-admin pages.

    Anonymous callers are sent to the login page with a ``?next=`` back-link;
    a signed-in non-admin gets a 403. Shared by the payments page and its CSV
    export so the two can't drift apart.
    """
    if not request.user.is_authenticated:
        return None, HttpResponseRedirect(
            reverse("login") + "?next=" + quote(request.path, safe="/:@")
        )
    race = get_object_or_404(Race, slug=race_slug)
    if not can_edit_race(request.user, race):
        return race, HttpResponseForbidden()
    return race, None


class RacePaymentsView(View):
    """Организаторская страница платежей гонки: реестр + итоги.

    Сервер отдаёт только строки (JSON-остров) — таблицу, плитки итогов,
    разбивку дохода и динамику по дням рисует ``payments.js``, как
    :class:`RaceTeamsView` рисует список команд. Ничего не пишет.
    """

    def get(self, request, race_slug):
        race, response = _load_race_for_admin(request, race_slug)
        if response is not None:
            return response

        # ``reverse()`` не умеет оставить плейсхолдер в пути, поэтому ссылка на
        # команду строится подстановкой — тот же приём, что в ``RaceMapView``.
        team_url = reverse("edit_team", kwargs={"team_id": 0})
        context = {
            "race": race,
            "payments_json": _safe_json(payment_rows(race)),
            "extras_json": _safe_json(extras_catalog(race)),
            "payments_config": _safe_json(
                {"teamUrlTemplate": re.sub(r"/0/$", "/{team_id}/", team_url)}
            ),
        }
        return render(request, "race/payments.html", context)


class RacePaymentsExportView(View):
    """CSV-выгрузка реестра платежей — те же строки, что видит страница.

    Разделитель ``;`` и BOM в начале: файл открывают в русском Excel. Это
    расхождение с выгрузкой команд (``api/views/teams.py``, без BOM) намеренное
    — ту читают скриптом.
    """

    def get(self, request, race_slug):
        race, response = _load_race_for_admin(request, race_slug)
        if response is not None:
            return response

        extras = extras_catalog(race)
        rows = filter_rows(
            payment_rows(race),
            request.GET.get("status"),
            promo_only=request.GET.get("promo") == "1",
        )
        today = datetime.date.today().isoformat()
        csv_response = HttpResponse(content_type="text/csv; charset=utf-8")
        csv_response["Content-Disposition"] = (
            f'attachment; filename="payments-{race.slug}-{today}.csv"'
        )
        csv_response.write("﻿")
        writer = csv.writer(csv_response, delimiter=";")
        for line in csv_rows(rows, extras):
            writer.writerow(line)
        return csv_response
