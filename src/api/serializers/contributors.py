from rest_framework import serializers

from donate.models import ClubMember, DonationPeriod, MemberDonation


class DonationPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = DonationPeriod
        fields = ["id", "name", "date", "is_active"]


class ClubMemberSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="notes")

    class Meta:
        model = ClubMember
        fields = ["id", "name", "label"]


class MemberDonationSerializer(serializers.ModelSerializer):
    member_id = serializers.IntegerField(source="member_id")
    period_id = serializers.IntegerField(source="period_id")
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)

    class Meta:
        model = MemberDonation
        fields = [
            "member_id",
            "period_id",
            "is_paid",
            "amount",
            "paid_date",
            "recipient",
            "note",
        ]
