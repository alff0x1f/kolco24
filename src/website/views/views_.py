import json
import random
import re
import uuid

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import (
    Http404,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views import View

from apps.race.permissions import can_edit_race
from apps.race.pricing import create_team_payment, upsert_team_extras
from apps.race.promo import PromoUnavailable
from website.forms import PageForm, TeamForm
from website.models import Race, RaceAdmin, Team
from website.models.race import Category, RegStatus

from ..models.news import MenuItem, Page


def is_race_admin(user, race):
    if not user.is_authenticated:
        return False
    return RaceAdmin.objects.filter(race=race, user=user).exists()


class RaceIdRedirectView(View):
    def get(self, request, race_id, **kwargs):
        race = get_object_or_404(Race, pk=race_id)
        new_path = request.path.replace(f"/race/{race_id}/", f"/race/{race.slug}/", 1)
        qs = f"?{request.GET.urlencode()}" if request.GET else ""
        return HttpResponsePermanentRedirect(new_path + qs)


def regulations(request):
    return render(request, "website/regulations.html")


def privacy_policy(request):
    """Display privacy policy page."""
    return HttpResponseRedirect(reverse("page", args=["privacy_app"]))


def refund_policy(request):
    """Display refund policy page."""
    return HttpResponseRedirect(reverse("page", args=["refund_policy"]))


def service_order_rules(request):
    """Display service order rules page."""
    return HttpResponseRedirect(reverse("page", args=["service_order_rules"]))


def rules(request):
    """Display service order rules page."""
    return HttpResponseRedirect(reverse("page", args=["rules"]))


def contacts(request):
    """Display contacts page."""
    return HttpResponseRedirect(reverse("page", args=["contacts"]))


def page(request, slug):
    """Display a static page based on the slug."""
    try:
        page = Page.objects.get(slug=slug)
    except Page.DoesNotExist:
        raise Http404("Page not found.")

    context = {
        "page": page,
        "menu": MenuItem.objects.all(),
        "is_moderator": request.user.is_authenticated
        and request.user.groups.filter(name="Moderators").exists(),
    }
    return render(request, "website/static_page.html", context=context)


@login_required
def edit_page(request, slug):
    """Allow moderators to edit a static page."""
    try:
        page = Page.objects.get(slug=slug)
    except Page.DoesNotExist:
        raise Http404("Page not found.")

    if not request.user.groups.filter(name="Moderators").exists():
        raise Http404("Page not found.")

    if request.method == "POST":
        form = PageForm(request.POST, instance=page)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(reverse("page", args=[page.slug]))
    else:
        form = PageForm(instance=page)

    context = {
        "form": form,
        "page": page,
        "menu": MenuItem.objects.all(),
        "is_moderator": True,
    }
    return render(request, "website/edit_page.html", context=context)


def build_category_options(race_id, current_category_id=None, team=None):
    """Category options carrying ``data-counts`` expanded from min/max people.

    If current_category_id names an inactive category, it is prepended so that
    existing teams whose category was later deactivated still render correctly.

    Each option also carries ``remaining`` — free slots for that category
    (``""`` when the category is unlimited), self-excluding ``team`` from the
    occupancy so a team editing within its own category isn't counted against
    its own limit. The client uses it to disable full categories.
    """
    cats = list(Category.objects.filter(race_id=race_id, is_active=True))
    if current_category_id is not None:
        active_ids = {c.id for c in cats}
        if current_category_id not in active_ids:
            current_cat = Category.objects.filter(
                id=current_category_id, race_id=race_id
            ).first()
            if current_cat:
                cats.insert(0, current_cat)
    options = []
    for category in cats:
        remaining = category.remaining_people(exclude_team=team)
        options.append(
            {
                "id": category.id,
                "label": f"{category.short_name} ({category.name})",
                "counts": list(range(category.min_people, category.max_people + 1)),
                "remaining": "" if remaining is None else int(remaining),
                "is_current": category.id == current_category_id,
            }
        )
    return options


def build_team_form_context(race, team, is_edit=False, bypass_limits=False, form=None):
    """Unified context shared by the add and edit team forms.

    The ``extras`` list (config island) mirrors ``compute_team_charge`` —
    see ``apps/race/pricing.py``. Keep the per-extra ``price``/``freePerTeam``/
    ``count``/``countPaid`` fields and the live-total formula in sync with
    ``src/static/js/team-form.js``.
    """
    current_category_id = getattr(team, "category2_id", None)
    price_tiers = race.price_tier_ladder()
    current_price = race.current_price
    race_remaining = race.remaining_people(exclude_team=team)

    team_extra_map = {}
    if team and team.pk:
        team_extra_map = {te.race_extra_id: te for te in team.extras.all()}
    extras_config = []
    for extra in race.extras.filter(is_active=True):
        te = team_extra_map.get(extra.id)
        count = te.count if te else 0
        # Preserve a submitted/invalid POST value across re-render when bound.
        if form is not None:
            field = f"extra_{extra.code}"
            if field in form.fields:
                try:
                    count = int(form[field].value() or 0)
                except (TypeError, ValueError):
                    count = te.count if te else 0
        extras_config.append(
            {
                "code": extra.code,
                "name": extra.name,
                "price": extra.price,
                "freePerTeam": extra.free_per_team,
                "count": count,
                "countPaid": te.count_paid if te else 0,
            }
        )

    # A resolved promo survives a re-render with errors; an unresolved one is
    # null and the field error explains why (see TeamForm.clean_promo_code).
    promo = getattr(form, "promo", None) if form is not None else None
    promo_config = (
        {"code": promo.code, "type": promo.discount_type, "value": promo.value}
        if promo is not None
        else None
    )

    config = {
        "currentPrice": current_price,
        "paidPeople": team.paid_people,
        "extras": extras_config,
        "isEdit": is_edit,
        "raceRemaining": race_remaining,
        "currentCategoryId": current_category_id,
        "bypassLimits": bypass_limits,
        "promo": promo_config,
        "promoCheckUrl": reverse("promo_check", args=[race.slug]),
        "teamId": team.pk,
        # Mirrors the reg_status gate in EditTeamView.post: with registration
        # closed the form only saves, it never goes to checkout.
        "canPay": race.reg_status == RegStatus.OPEN,
    }
    return {
        "current_price": current_price,
        "paid_people": team.paid_people,
        "price_tiers": price_tiers,
        "reg_open": race.reg_status == RegStatus.OPEN,
        "is_editable": race.is_teams_editable,
        "reg_status": race.reg_status,
        "extras": extras_config,
        "category_options": build_category_options(
            race.id, current_category_id, team=team
        ),
        "team_form_config_json": mark_safe(json.dumps(config)),
    }


SUBMIT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _category_from_post(raw_id, race):
    """Race category named by a posted ``category2_id``, or ``None``.

    Missing, blank and non-numeric values all mean «no category» — the form
    turns that into a field error instead of a bare 404.
    """
    try:
        category_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    return Category.objects.filter(id=category_id, race_id=race.id).first()


class AddTeam(View):
    def get(self, request, race_slug):
        race = get_object_or_404(Race, slug=race_slug)

        if not request.user.is_authenticated:
            return HttpResponseRedirect(
                reverse("account_start") + f"?next={request.path}"
            )
        form = TeamForm(race.id)
        bypass = can_edit_race(request.user, race)
        return render(
            request,
            "website/add_team.html",
            {
                "race": race,
                "race_id": race.id,
                "team_form": form,
                "team": Team(),
                "action": reverse("add_team", args=[race.slug]),
                "submit_token": uuid.uuid4().hex,
                **build_team_form_context(race, Team(), bypass_limits=bypass),
            },
        )

    @staticmethod
    def _submitted_team(request, token):
        if not token:
            return None
        return Team.all_objects.filter(
            owner_id=request.user.id, submit_token=token
        ).first()

    @staticmethod
    def _resume(race, team):
        """Answer a re-post of a form that already created ``team``."""
        if team.is_deleted:
            return HttpResponseRedirect(reverse("my_teams", args=[race.slug]))
        # The first post may still be creating the VTB order; team_checkout
        # tells that apart from a ready order and from a failed checkout.
        return HttpResponseRedirect(reverse("team_checkout", args=[team.id]))

    def post(self, request, race_slug):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(
                reverse("account_start") + f"?next={request.path}"
            )

        race = get_object_or_404(Race, slug=race_slug)
        if not race.is_teams_editable:
            return HttpResponse("Регистрация закрыта")

        # A select whose options are all disabled (race full) submits no value
        # at all, so the category may be missing: the form reports it.
        category2 = _category_from_post(request.POST.get("category2_id"), race)

        payment_method = request.GET.get("method", "sbp2")
        if payment_method != "sbp2":
            raise Http404

        submit_token = request.POST.get("submit_token", "")
        if not SUBMIT_TOKEN_RE.match(submit_token):
            submit_token = ""
        # Checked before the form: the team from the first post already holds
        # its seats, so re-validating could fail the capacity check on itself.
        existing = self._submitted_team(request, submit_token)
        if existing is not None:
            return self._resume(race, existing)

        data = request.POST.copy()
        data["dist"] = category2.code if category2 else ""
        data["paymentid"] = "%016x" % random.randrange(16**16)  # legacy
        bypass = can_edit_race(request.user, race)
        form = TeamForm(race.id, data, bypass_limits=bypass)
        if form.is_valid():
            # save team (extra_<code> fields are add-ons, promo_code is the
            # promo input — neither is a Team column)
            team_fields = {
                k: v
                for k, v in form.cleaned_data.items()
                if not k.startswith("extra_") and k != "promo_code"
            }
            try:
                with transaction.atomic():
                    team: Team = Team.objects.create(
                        year=race.date.year,
                        owner_id=request.user.id,
                        submit_token=submit_token,
                        **team_fields,
                    )
            except IntegrityError:
                # A parallel post of the same form won the insert.
                existing = self._submitted_team(request, submit_token)
                if existing is None:
                    raise
                return self._resume(race, existing)

            upsert_team_extras(team, form.cleaned_data, race)

            if race.reg_status != RegStatus.OPEN:
                return HttpResponseRedirect(reverse("my_teams", args=[race.slug]))

            # payment (race fee + add-on deltas, one VTB/SBP order)
            try:
                response = create_team_payment(request, team, race, promo=form.promo)
            except PromoUnavailable as exc:
                # The code's last slot went to someone else between validation
                # and checkout. Drop the just-created team (it has no payment
                # and nothing was credited) so the retry does not duplicate it.
                team.delete()
                form.add_error(None, str(exc))
            else:
                if response is None:
                    return HttpResponseRedirect(reverse("my_teams", args=[race.slug]))
                return response

        return render(
            request,
            "website/add_team.html",
            {
                "race": race,
                "race_id": race.id,
                "team_form": form,
                "team": Team(),
                "action": reverse("add_team", args=[race.slug]),
                "submit_token": submit_token or uuid.uuid4().hex,
                **build_team_form_context(
                    race, Team(), bypass_limits=bypass, form=form
                ),
            },
        )
