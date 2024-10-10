from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from website.forms import TeamForm, TeamMemberMoveForm
from website.models import Payment, PaymentsYa, Team, TeamMemberMove


class EditTeamView(View):
    def get(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("passlogin") + f"?next={request.path}")

        team: Team = self.get_team(team_id)
        if not team:
            return HttpResponseRedirect(reverse("passlogin") + f"?next={request.path}")

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
        form = TeamForm(team.category2.race_id, initial=initial)

        # Disable all form fields
        if not team.category2.race.is_teams_editable and not request.user.is_superuser:
            for field in form.fields.values():
                field.disabled = True

        return render(
            request,
            "website/add_team.html",
            {
                "race_id": team.category2.race_id,
                "team_form": form,
                "team": team,
                "cost": PaymentsYa.get_cost(),
                "reg_open": settings.REG_OPEN,
                "action": reverse("edit_team", args=[team_id]),
                "payments": Payment.objects.filter(team=team, status="done").order_by(
                    "id"
                ),
                "member_moves": TeamMemberMove.objects.filter(
                    Q(from_team=team) | Q(to_team=team)
                ).order_by("id"),
                "team_move_form": TeamMemberMoveForm(race_id=team.category2.race_id),
            },
        )

    def post(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("passlogin") + f"?next={request.path}")

        team: Team = self.get_team(team_id)
        if not team:
            return HttpResponseRedirect(reverse("passlogin") + f"?next={request.path}")

        if not team.category2.race.is_teams_editable and not request.user.is_superuser:
            return HttpResponse("Редактирование команд запрещено", status=403)

        form = TeamForm(team.category2.race_id, request.POST)
        if form.is_valid():
            if "teamname" in form.cleaned_data:
                team.teamname = form.cleaned_data.get("teamname")
            if "city" in form.cleaned_data:
                team.city = form.cleaned_data.get("city")
            if "organization" in form.cleaned_data:
                team.organization = form.cleaned_data.get("organization")
            if "ucount" in form.cleaned_data:
                team.ucount = form.cleaned_data.get("ucount")

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
            if "map_count" in form.cleaned_data:
                team.map_count = form.cleaned_data.get("map_count", 0)

            team.save()
            return HttpResponseRedirect(
                reverse("teams2", args=[team.category2.race_id, team.category2_id])
            )

        # If form is not valid, re-render the form with errors
        return render(
            request,
            "website/add_team.html",
            {
                "race_id": team.category2.race_id,
                "team_form": form,
                "team": team,
                "cost": PaymentsYa.get_cost(),
                "reg_open": settings.REG_OPEN,
                "action": reverse("edit_team", args=[team_id]),
                "payments": Payment.objects.filter(team=team, status="done").order_by(
                    "id"
                ),
                "member_moves": TeamMemberMove.objects.filter(
                    Q(from_team=team) | Q(to_team=team)
                ).order_by("id"),
                "team_move_form": TeamMemberMoveForm(race_id=team.category2.race_id),
            },
        )

    def get_team(self, team_id):
        qs = Team.objects.filter(id=team_id).select_related("category2")
        if not self.request.user.is_superuser:
            qs = qs.filter(owner_id=self.request.user.id)
        return qs.first()


class TeamMemberMoveView(View):
    def post(self, request, team_id):
        """Перемещение участника из команды в команду"""
        if not request.user.is_superuser:
            return HttpResponseRedirect(reverse("passlogin") + f"?next={request.path}")

        data = request.POST.copy()
        data["from_team"] = Team.objects.filter(id=team_id).first().id
        form = TeamMemberMoveForm(data)
        if form.is_valid():
            form.save()
            form.instance.move_people()
            return HttpResponseRedirect(reverse("edit_team", args=[team_id]))
        return HttpResponse(f"Ошибка: {form.errors}", status=400)
