from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from apps.race.pricing import create_team_payment, upsert_team_extras
from website.forms import TeamForm, TeamMemberMoveForm
from website.models import Payment, Team, TeamMemberMove
from website.models.race import RegStatus
from website.views.views_ import build_team_form_context


class EditTeamView(View):
    def get(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("login") + f"?next={request.path}")

        team: Team = self.get_team(team_id)
        if not team:
            raise Http404

        race = team.category2.race

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
                "payments": Payment.objects.filter(team=team, status="done").order_by(
                    "id"
                ),
                "member_moves": TeamMemberMove.objects.filter(
                    Q(from_team=team) | Q(to_team=team)
                ).order_by("id"),
                "team_move_form": TeamMemberMoveForm(race_id=team.category2.race_id),
                **build_team_form_context(
                    race, team, is_edit=True, bypass_limits=request.user.is_superuser
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

        if not team.category2.race.is_teams_editable and not request.user.is_superuser:
            return HttpResponse("Редактирование команд запрещено", status=403)

        if request.POST.get("delete_team"):
            return self.delete_team(request, team)

        form = TeamForm(
            team.category2.race_id,
            request.POST,
            current_category_id=team.category2_id,
            team=team,
            bypass_limits=request.user.is_superuser,
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
                        "payments": Payment.objects.filter(
                            team=team, status="done"
                        ).order_by("id"),
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
                            bypass_limits=request.user.is_superuser,
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
            response = create_team_payment(request, team, race)
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
                "payments": Payment.objects.filter(team=team, status="done").order_by(
                    "id"
                ),
                "member_moves": TeamMemberMove.objects.filter(
                    Q(from_team=team) | Q(to_team=team)
                ).order_by("id"),
                "team_move_form": TeamMemberMoveForm(race_id=team.category2.race_id),
                **build_team_form_context(
                    race,
                    team,
                    is_edit=True,
                    bypass_limits=request.user.is_superuser,
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
        team.save(update_fields=["is_deleted"])

        return HttpResponseRedirect(
            reverse("my_teams", args=[team.category2.race.slug])
        )

    def get_team(self, team_id):
        qs = Team.objects.filter(id=team_id).select_related("category2__race")
        if not self.request.user.is_superuser:
            qs = qs.filter(owner_id=self.request.user.id)
        return qs.first()


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
