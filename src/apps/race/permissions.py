"""Access-control helpers for the race app."""

from website.models import RaceAdmin, Team

TEAMS_ALL = "all"
TEAMS_OWN = "own"
TEAMS_NONE = "none"


def is_team_editing_open(user, race):
    """Whether team editing is open; callers must separately check ownership."""
    return race.is_teams_editable or bool(user and user.is_superuser)


def can_edit_race(user, race):
    if not user.is_authenticated:
        return False
    return RaceAdmin.objects.filter(
        race=race, user=user, role=RaceAdmin.Role.ADMIN
    ).exists()


def teams_access(user, race):
    """How much of the race's team list ``user`` may see."""
    if race.show_teams:
        return TEAMS_ALL
    if user is None or not user.is_authenticated:
        return TEAMS_NONE
    if user.is_superuser or RaceAdmin.objects.filter(race=race, user=user).exists():
        return TEAMS_ALL
    if Team.objects.filter(category2__race=race, owner=user).exists():
        return TEAMS_OWN
    return TEAMS_NONE
