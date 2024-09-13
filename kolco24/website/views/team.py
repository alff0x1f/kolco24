from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from website.forms import TeamForm
from website.models import Team, PaymentsYa


class EditTeamView(View):
    def get(self, request, team_id):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse("passlogin") + f"?next={request.path}")

        qs = Team.objects.filter(id=team_id).select_related("category2")
        if not request.user.is_superuser:
            qs = qs.filter(owner_id=request.user.id)
        team: Team = qs.first()

        if not team:
            return JsonResponse({"error": "Team not found"}, status=404)

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
            "burth1": team.birth1,
            "burth2": team.birth2,
            "burth3": team.birth3,
            "burth4": team.birth4,
            "burth5": team.birth5,
            "burth6": team.birth6,
        }
        form = TeamForm(team.category2.race_id, initial=initial)
        return render(
            request,
            "website/add_team.html",
            {
                "race_id": team.category2.race_id,
                "team_form": form,
                "team": team,
                "cost": PaymentsYa.get_cost(),
            },
        )
