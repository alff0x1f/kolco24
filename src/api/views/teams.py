import csv

from django.http import HttpResponse
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView
from website.models import Team

from api.serializers.team import TeamSerializer


class TeamListView(ListAPIView):
    """Эндпоинт для получения списка Команд"""

    serializer_class = TeamSerializer

    def get_queryset(self):
        race_id = self.kwargs.get("race_id")
        return Team.objects.select_related("category2").filter(
            category2__race_id=race_id, paid_people__gt=0
        )


class TeamCSVListView(APIView):
    """Endpoint to return a list of Teams in CSV format"""

    def get(self, request, *args, **kwargs):
        # Get the queryset
        race_id = self.kwargs.get("race_id")
        teams = (
            Team.objects.select_related("category2", "owner", "owner__profile")
            .filter(category2__race_id=race_id, paid_people__gt=0)
            .order_by("category2", "start_number", "id")
        )

        # Create the HttpResponse with the CSV content type
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="teams.csv"'

        # Create a CSV writer
        writer = csv.writer(response, delimiter=";")

        # Write the headers
        writer.writerow(
            [
                "ID",
                "Owner",
                "Email",
                "Phone",
                "Team Name",
                "Paid People",
                "Ucount",
                "Category",
                "Start Number",
                "Athlete 1",
                "Birth 1",
                "Athlete 2",
                "Birth 2",
                "Athlete 3",
                "Birth 3",
                "Athlete 4",
                "Birth 4",
                "Athlete 5",
                "Birth 5",
                "Athlete 6",
            ]
        )

        # Write data rows
        for team in teams:
            writer.writerow(
                [
                    team.id,
                    f"{team.owner.last_name} {team.owner.first_name}",
                    team.owner.email,
                    team.owner.profile.phone,
                    team.teamname,
                    round(team.paid_people),
                    team.ucount,
                    team.category2.short_name,
                    team.start_number,
                    team.athlet1,
                    team.birth1,
                    team.athlet2,
                    team.birth2,
                    team.athlet3,
                    team.birth3,
                    team.athlet4,
                    team.birth4,
                    team.athlet5,
                    team.birth5,
                    team.athlet6,
                    team.birth6,
                ]
            )

        # Return the response as a CSV file
        return response
