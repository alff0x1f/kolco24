from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.contributors import (
    ClubMemberSerializer,
    DonationPeriodSerializer,
    MemberDonationSerializer,
)
from donate.models import ClubMember, DonationPeriod, MemberDonation


class BearerTokenPermission(BasePermission):
    def has_permission(self, request, view):
        token = getattr(settings, "CONTRIBUTORS_API_TOKEN", None)
        if not token:
            return False
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return auth[len("Bearer ") :] == token


class ContributorsView(APIView):
    permission_classes = [BearerTokenPermission]
    authentication_classes = []

    def get(self, request):
        periods = DonationPeriod.objects.all()
        members = ClubMember.objects.all()
        donations = MemberDonation.objects.all()

        return Response(
            {
                "periods": DonationPeriodSerializer(periods, many=True).data,
                "members": ClubMemberSerializer(members, many=True).data,
                "donations": MemberDonationSerializer(donations, many=True).data,
            }
        )
