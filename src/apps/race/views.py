import datetime
import json
import re
from urllib.parse import quote

from django.db import transaction
from django.db.models import Count, OuterRef, ProtectedError, Q, Subquery
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views import View

from apps.race.forms import RaceForm
from apps.race.models import RaceExtra
from apps.race.permissions import can_edit_race
from website.forms import NewsPostForm
from website.models import Checkpoint, NewsPost, Race, Team
from website.models.checkpoint import CheckpointTag
from website.models.enums import CheckpointType
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
        news_qs = NewsPost.objects.filter(race=race).order_by("-publication_date")
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
        }
        context["can_edit_race"] = bool(user is not None and can_edit_race(user, race))
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


def _validate_legend_rows(rows):
    """Validate parsed legend (checkpoint) rows.

    Returns ``(cleaned, errors)`` like :func:`_validate_category_rows`.
    ``number``/``cost`` are non-negative integers (empty → ``0``); ``type`` must
    be a valid :class:`CheckpointType` (empty → ``kp``); ``description`` is
    trimmed and capped at the model's 200-char limit; ``is_legend_locked`` is a
    bool.
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
        extras_data=None,
        category_errors=None,
        price_tier_errors=None,
        extra_errors=None,
    ):
        if form is None:
            form = RaceForm(instance=race)
        if categories_data is None:
            categories_data = [] if race is None else self._existing_categories(race)
        if price_tiers_data is None:
            price_tiers_data = [] if race is None else self._existing_price_tiers(race)
        if extras_data is None:
            extras_data = [] if race is None else self._existing_extras(race)
        return {
            "race": race,
            "form": form,
            "is_create": race is None,
            "categories_data": _safe_json(categories_data),
            "price_tiers_data": _safe_json(price_tiers_data),
            "extras_data": _safe_json(extras_data),
            "category_errors": _safe_json(category_errors or {}),
            "price_tier_errors": _safe_json(price_tier_errors or {}),
            "extra_errors": _safe_json(extra_errors or {}),
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

        category_errors = {}
        price_tier_errors = {}
        extra_errors = {}
        cleaned_categories = cleaned_tiers = cleaned_extras = None
        if category_rows is not None:
            cleaned_categories, category_errors = _validate_category_rows(category_rows)
        if price_tier_rows is not None:
            cleaned_tiers, price_tier_errors = _validate_price_tier_rows(
                price_tier_rows
            )
        if extra_rows is not None:
            cleaned_extras, extra_errors = _validate_extra_rows(extra_rows)

        if category_errors:
            bad = ", ".join(str(i + 1) for i in sorted(category_errors))
            form.add_error(None, f"Ошибки в категориях (строки: {bad}).")
        if price_tier_errors:
            bad = ", ".join(str(i + 1) for i in sorted(price_tier_errors))
            form.add_error(None, f"Ошибки в ценовых периодах (строки: {bad}).")
        if extra_errors:
            bad = ", ".join(str(i + 1) for i in sorted(extra_errors))
            form.add_error(None, f"Ошибки в доп-услугах (строки: {bad}).")

        if (
            form_valid
            and category_rows is not None
            and price_tier_rows is not None
            and extra_rows is not None
            and not category_errors
            and not price_tier_errors
            and not extra_errors
        ):
            try:
                with transaction.atomic():
                    race = form.save()
                    _reconcile_categories(race, cleaned_categories)
                    _reconcile_price_tiers(race, cleaned_tiers)
                    _reconcile_extras(race, cleaned_extras)
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

        context = self._build_context(
            render_race,
            form=form,
            categories_data=category_rows or [],
            price_tiers_data=price_tier_rows or [],
            extras_data=extra_rows or [],
            category_errors=category_errors,
            price_tier_errors=price_tier_errors,
            extra_errors=extra_errors,
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
            CheckpointTag.objects.filter(point__race_id=race.id)
            .select_related("point")
            .order_by("point__number", "id")
        )
        rows = [
            {
                "nfc_uid": tag.nfc_uid,
                "number": tag.point.number,
                "code": bytes(tag.code).hex() if tag.code else "—",
            }
            for tag in tags
        ]
        return render(request, "race/legend_codes.html", {"race": race, "rows": rows})
