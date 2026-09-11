"""Access-control helpers for the race app."""

from website.models import RaceAdmin


def is_team_editing_open(user, race):
    """Whether team editing is open; callers must separately check ownership."""
    return race.is_teams_editable or bool(user and user.is_superuser)


def can_edit_race(user, race):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return RaceAdmin.objects.filter(
        race=race, user=user, role=RaceAdmin.Role.ADMIN
    ).exists()
