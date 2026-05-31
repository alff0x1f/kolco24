"""Access-control helpers for the race app."""

from website.models import RaceAdmin


def can_edit_race(user, race):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return RaceAdmin.objects.filter(
        race=race, user=user, role=RaceAdmin.Role.ADMIN
    ).exists()
