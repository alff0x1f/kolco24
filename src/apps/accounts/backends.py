from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Custom authentication backend that allows users to authenticate using email.

    instead of username
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()

        # Try to fetch the user by email
        try:
            user = UserModel.objects.get(email__iexact=username)
        except (UserModel.DoesNotExist, UserModel.MultipleObjectsReturned):
            return None

        # If user is found, check password and active status
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
