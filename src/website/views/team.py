from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from apps.race.permissions import can_edit_race
from apps.race.pricing import (
    CheckoutInFlight,
    checkout_in_flight,
    create_team_payment,
    open_pay_redirect,
    upsert_team_extras,
)
from apps.race.promo import PromoUnavailable
from website.forms import TeamForm, TeamMemberMoveForm
from website.models import Payment, Team, TeamMemberMove
from website.models.race import RegStatus
from website.views.views_ import build_team_form_context


def payment_history(team: Team) -> list[dict]:
    """Строки «Истории оплат»: платежи и возвраты по ним, по дате.

    Полный возврат переводит платёж ``done → cancel`` (см. settlement.py), так
    что фильтр по одному ``done`` прятал бы возвращённый платёж целиком — вместе
    с самим фактом возврата.
    """
    rows = []
    payments = (
        Payment.objects.filter(team=team, status__in=("done", "cancel"))
        .prefetch_related("refunds")
        .order_by("id")
    )
    for payment in payments:
        if payment.payment_amount:
            rows.append(
                {
                    "kind": "payment",
                    "people": payment.paid_for,
                    "date": payment.created_at,
                    "amount": payment.payment_amount,
                }
            )
        for refund in payment.refunds.all():
            # Нулевая строка — только аудит банка, денег она не двигала.
            if not refund.amount:
                continue
            rows.append(
                {
                    "kind": "refund",
                    "people": refund.people,
                    "date": refund.refunded_at or payment.updated_at,
                    "amount": -refund.amount,
                }
            )
    rows.sort(key=lambda row: row["date"])
    return rows


class EditTeamView(View):
    def get(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("login") + f"?next={request.path}")

        team: Team = self.get_team(team_id)
        if not team:
            raise Http404

        race = team.category2.race
        bypass = can_edit_race(request.user, race)

        initial = {
            "teamname": team.teamname,
            "category2_id": team.category2.id,
            "city": team.city,
            "organization": team.organization,
            "ucount": team.ucount,
            "athlet1": team.athlet1,
            "athlet2": team.athlet2,
            "athlet3": team.athlet3,
            "athlet4": team.athlet4,
            "athlet5": team.athlet5,
            "athlet6": team.athlet6,
            "birth1": team.birth1,
            "birth2": team.birth2,
            "birth3": team.birth3,
            "birth4": team.birth4,
            "birth5": team.birth5,
            "birth6": team.birth6,
        }
        form = TeamForm(team.category2.race_id, initial=initial, team=team)

        # Disable all form fields
        if not team.category2.race.is_teams_editable and not request.user.is_superuser:
            for field in form.fields.values():
                field.disabled = True

        return render(
            request,
            "website/edit_team.html",
            {
                "race_id": team.category2.race_id,
                "race": race,
                "team_form": form,
                "team": team,
                "action": reverse("edit_team", args=[team_id]),
                "payment_history": payment_history(team),
                "member_moves": TeamMemberMove.objects.filter(
                    Q(from_team=team) | Q(to_team=team)
                ).order_by("id"),
                "team_move_form": TeamMemberMoveForm(race_id=team.category2.race_id),
                **build_team_form_context(
                    race, team, is_edit=True, bypass_limits=bypass
                ),
            },
        )

    def post(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("login") + f"?next={request.path}")

        team: Team = self.get_team(team_id)
        if not team:
            raise Http404
        race = team.category2.race
        bypass = can_edit_race(request.user, race)

        if not team.category2.race.is_teams_editable and not request.user.is_superuser:
            return HttpResponse("Редактирование команд запрещено", status=403)

        if request.POST.get("delete_team"):
            return self.delete_team(request, team)

        if checkout_in_flight(team):
            return HttpResponseRedirect(reverse("team_checkout", args=[team.id]))

        form = TeamForm(
            team.category2.race_id,
            request.POST,
            current_category_id=team.category2_id,
            team=team,
            bypass_limits=bypass,
        )
        if form.is_valid():
            if "teamname" in form.cleaned_data:
                team.teamname = form.cleaned_data.get("teamname")
            if "city" in form.cleaned_data:
                team.city = form.cleaned_data.get("city")
            if "organization" in form.cleaned_data:
                team.organization = form.cleaned_data.get("organization")

            new_ucount = int(form.cleaned_data.get("ucount"))
            if new_ucount < team.paid_people:
                form.add_error(
                    "ucount",
                    "Нельзя уменьшить количество участников: часть уже оплачена.",
                )
                return render(
                    request,
                    "website/edit_team.html",
                    {
                        "race": race,
                        "race_id": race.id,
                        "team_form": form,
                        "team": team,
                        "action": reverse("edit_team", args=[team_id]),
                        "payment_history": payment_history(team),
                        "member_moves": TeamMemberMove.objects.filter(
                            Q(from_team=team) | Q(to_team=team)
                        ).order_by("id"),
                        "team_move_form": TeamMemberMoveForm(
                            race_id=team.category2.race_id
                        ),
                        **build_team_form_context(
                            race,
                            team,
                            is_edit=True,
                            bypass_limits=bypass,
                            form=form,
                        ),
                    },
                )
            if "ucount" in form.cleaned_data:
                team.ucount = new_ucount

            # Loop through athlete and birth fields to update them conditionally
            for i in range(1, 7):
                athlet_field = f"athlet{i}"
                birth_field = f"birth{i}"
                if athlet_field in form.cleaned_data:
                    setattr(team, athlet_field, form.cleaned_data.get(athlet_field))
                if birth_field in form.cleaned_data:
                    setattr(team, birth_field, form.cleaned_data.get(birth_field))

            if "category2_id" in form.cleaned_data:
                team.category2_id = form.cleaned_data.get("category2_id")

            with transaction.atomic():
                team.save()
                upsert_team_extras(team, form.cleaned_data, race)

            if race.reg_status != RegStatus.OPEN:
                return HttpResponseRedirect(reverse("my_teams", args=[race.slug]))

            # payment (race fee + add-on deltas, one VTB/SBP order)
            try:
                response = create_team_payment(request, team, race, promo=form.promo)
            except CheckoutInFlight:
                return HttpResponseRedirect(reverse("team_checkout", args=[team.id]))
            except PromoUnavailable as exc:
                # Quota taken between validation and checkout: show the form
                # again instead of silently charging the full price. The team
                # edits are already saved, like any abandoned payment.
                form.add_error(None, str(exc))
            else:
                if response is not None:
                    return response

                return HttpResponseRedirect(
                    reverse("teams2", args=[race.slug, team.category2_id])
                )

        # If form is not valid, re-render the form with errors
        return render(
            request,
            "website/edit_team.html",
            {
                "race": race,
                "race_id": race.id,
                "team_form": form,
                "team": team,
                "action": reverse("edit_team", args=[team_id]),
                "payment_history": payment_history(team),
                "member_moves": TeamMemberMove.objects.filter(
                    Q(from_team=team) | Q(to_team=team)
                ).order_by("id"),
                "team_move_form": TeamMemberMoveForm(race_id=team.category2.race_id),
                **build_team_form_context(
                    race,
                    team,
                    is_edit=True,
                    bypass_limits=bypass,
                    form=form,
                ),
            },
        )

    def delete_team(self, request, team: Team):
        if not (request.user.is_superuser or team.owner_id == request.user.id):
            return HttpResponse("Удаление запрещено", status=403)

        if not team.can_be_deleted:
            return HttpResponse("Команду нельзя удалить", status=400)

        team.is_deleted = True
        team.save(update_fields=["is_deleted", "updated_at"])

        return HttpResponseRedirect(
            reverse("my_teams", args=[team.category2.race.slug])
        )

    def get_team(self, team_id):
        qs = Team.objects.filter(id=team_id).select_related("category2__race")
        if not self.request.user.is_superuser:
            qs = qs.filter(owner_id=self.request.user.id)
        return qs.first()


class TeamCheckoutView(View):
    """Where a repeated checkout lands: pay, wait for the order, or edit.

    A repeat can arrive while the first request is still creating the VTB order
    — then there is nothing to pay yet, and the edit form would offer a second
    order for the same seats. This page waits for the order instead.
    """

    def get(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("login") + f"?next={request.path}")

        qs = Team.objects.filter(id=team_id)
        if not request.user.is_superuser:
            qs = qs.filter(owner_id=request.user.id)
        team = qs.first()
        if not team:
            raise Http404

        if checkout_in_flight(team):
            return render(
                request,
                "website/checkout_pending.html",
                {"team": team, "refresh_url": request.path},
            )
        response = open_pay_redirect(team)
        if response is not None:
            return response
        return HttpResponseRedirect(reverse("edit_team", args=[team.id]))


class TeamMemberMoveView(View):
    def post(self, request, team_id):
        """Перемещение участника из команды в команду"""
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("login") + f"?next={request.path}")

        from_team = Team.objects.filter(id=team_id).first()
        if not from_team:
            return HttpResponse("Команда недоступна", status=404)

        if not (request.user.is_superuser or from_team.owner_id == request.user.id):
            return HttpResponse("Перенос доступен только владельцу команды", status=403)

        data = request.POST.copy()
        data["from_team"] = from_team.id
        form = TeamMemberMoveForm(data, race_id=from_team.category2.race_id)
        if form.is_valid():
            form.save()
            form.instance.move_people()
            return HttpResponseRedirect(reverse("edit_team", args=[team_id]))
        return HttpResponse(f"Ошибка: {form.errors}", status=400)
