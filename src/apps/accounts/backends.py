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
        except UserModel.DoesNotExist:
            # Run the default password hasher once to reduce the timing
            # difference between an existing and a nonexistent user (#20760).
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            # Run the dummy hash here too so this branch is not faster than the
            # normal wrong-password path and cannot be distinguished by timing.
            UserModel().set_password(password)
            return None

        # If user is found, check password and active status
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
