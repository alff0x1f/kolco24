def can_moderate(user, contest):
    """Only an explicit moderator row counts; superuser status grants nothing."""
    return user.is_authenticated and contest.moderators.filter(user=user).exists()
