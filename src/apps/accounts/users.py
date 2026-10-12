from django.contrib.auth import get_user_model


def find_user(query):
    """Look a user up by numeric ID or, otherwise, by email (case-insensitive)."""
    user_model = get_user_model()
    # isdigit() alone also accepts "²"/"①", which int() rejects.
    if query.isascii() and query.isdigit():
        if len(query) > 18:
            return None
        return user_model.objects.filter(pk=int(query)).first()
    return user_model.objects.filter(email__iexact=query).order_by("pk").first()
