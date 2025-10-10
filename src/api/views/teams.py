import csv

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.team import (
    TeamFinishSerializer,
    TeamSerializer,
    TeamStartSerializer,
)
from website.models import Race, Tag, Team, TeamFinishLog, TeamStartLog


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
                "City",
                "Organization",
                "Paid People",
                "Count",
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
                    (
                        f"{team.owner.last_name} {team.owner.first_name}"
                        if team.owner_id != 1
                        else ""
                    ),
                    team.owner.email if team.owner_id != 1 else "",
                    team.owner.profile.phone if team.owner_id != 1 else "",
                    team.teamname,
                    team.city,
                    team.organization,
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


class TeamStartView(APIView):
    """Endpoint to record team start events"""

    def post(self, request, race_id):
        serializer = TeamStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        race = get_object_or_404(Race, pk=race_id)
        team = get_object_or_404(
            Team.objects.select_related("category2"), pk=data["team_id"]
        )

        if not team.category2 or team.category2.race_id != race.id:
            return Response(
                {"team_id": ["Команда не относится к указанной гонке"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            TeamStartLog.objects.create(
                race=race,
                team=team,
                start_number=data["start_number"],
                team_name=data["team_name"],
                participant_count=data["participant_count"],
                scanned_count=data["scanned_count"],
                member_tags=data["member_tags"],
                start_timestamp=data["start_timestamp"],
            )

            team.start_time = data["start_timestamp"]
            team.save(update_fields=["start_time"])

        return Response({"success": True})


class TeamFinishView(APIView):
    """Endpoint to record team finish events"""

    def post(self, request, race_id):
        serializer = TeamFinishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        race = get_object_or_404(Race, pk=race_id)

        tag_uid = data["tag_uid"].strip()
        member_tag_id = data.get("member_tag_id")
        recorded_at = data["recorded_at"]

        normalized_tag_uid = tag_uid.upper()

        tag = Tag.objects.filter(id=member_tag_id, tag_id=normalized_tag_uid).first()
        if not tag:
            return Response(
                {"member_tag_id": ["Указанный тег не найден"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        team_start_log = (
            TeamStartLog.objects.select_related("team")
            .filter(race=race, member_tags__contains=normalized_tag_uid)
            .order_by("-created_at")
            .first()
        )

        if not team_start_log or not team_start_log.team:
            return Response(
                {"detail": "Команда для указанного тега не найдена"},
                status=status.HTTP_404_NOT_FOUND,
            )

        team = team_start_log.team

        with transaction.atomic():
            TeamFinishLog.objects.create(
                race=race,
                team=team,
                member_tag_id=member_tag_id,
                tag_uid=normalized_tag_uid,
                recorded_at=recorded_at,
            )

            team.finish_time = recorded_at
            team.save(update_fields=["finish_time"])

        return Response({"success": True})
