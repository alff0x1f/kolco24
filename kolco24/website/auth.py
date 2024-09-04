from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """
    Custom authentication backend that allows users to authenticate using email instead of username.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()

        # Try to fetch the user by email
        try:
            user = UserModel.objects.get(email=username)
        except UserModel.DoesNotExist:
            return None

        # If user is found, check if the password is correct
        if user.check_password(password):
            return user
        return None
