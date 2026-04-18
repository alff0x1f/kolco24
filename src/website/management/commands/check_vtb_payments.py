from time import sleep

from django.core.management.base import BaseCommand
from django.utils import timezone

from donate.models import ClubMember, DonateRequest, DonationPeriod, MemberDonation
from vtb.client import VTBClient
from website.models import Payment, Team, VTBPayment


class Command(BaseCommand):
    help = "Check VTB payments status and update related Payment and Team if paid."
    donate_prefix = "SPUTNIK"

    def handle(self, *args, **options):
        client = VTBClient()
        while True:
            payments = VTBPayment.objects.exclude(status__iexact="PAID").exclude(
                status__iexact="EXPIRED"
            )
            if not payments:
                self.stdout.write("No pending VTB payments found, sleeping...")
                sleep(60)
                continue
            for vtb_payment in payments:
                sleep(3)  # avoid hitting rate limits
                self.stdout.write(f"Checking VTB payment {vtb_payment.pk}...")

                try:
                    payload = client.get_order(vtb_payment.order_id)
                except Exception as exc:  # pragma: no cover - network/HTTP failures
                    self.stderr.write(
                        f"Failed to fetch order {vtb_payment.order_id}: {exc}"
                    )
                    continue
                new_status = (
                    payload.get("object", {}).get("status", {}).get("value", "")
                )
                if new_status == "EXPIRED":
                    vtb_payment.status = new_status
                    vtb_payment.status_description = (
                        payload.get("object", {})
                        .get("status", {})
                        .get("description", "")
                    )
                    vtb_payment.save(update_fields=["status", "status_description"])
                    self.stdout.write(f"Payment {vtb_payment.pk} marked as expired")
                    continue

                if new_status.upper() == "PAID":
                    vtb_payment.status = new_status
                    vtb_payment.status_description = (
                        payload.get("object", {})
                        .get("status", {})
                        .get("description", "")
                    )
                    vtb_payment.save(update_fields=["status", "status_description"])
                    self.stdout.write(f"Payment {vtb_payment.pk} marked as paid")

                    if vtb_payment.order_id.startswith(f"{self.donate_prefix}_"):
                        self._process_donation(vtb_payment)
                        continue

                    # order_id has format ORDER_<payment_id>
                    try:
                        payment_id = int(vtb_payment.order_id.split("_")[-1])
                    except (ValueError, AttributeError):
                        continue
                    payment: Payment = Payment.objects.filter(pk=payment_id).first()
                    if not payment or payment.status == Payment.STATUS_DONE:
                        continue
                    team: Team = payment.team
                    if team:
                        team.paid_people += payment.paid_for
                        team.paid_sum += payment.payment_amount
                        team.map_count_paid += payment.map
                        team.save(
                            update_fields=["paid_people", "paid_sum", "map_count_paid"]
                        )
                    payment.status = Payment.STATUS_DONE
                    payment.order = payment.pk
                    payment.save(update_fields=["status", "order"])
                    self.stdout.write(f"Payment {payment.pk} marked as paid")
            sleep(60)

    def _process_donation(self, vtb_payment: VTBPayment) -> None:
        """Create or update MemberDonation when a SPUTNIK_* payment is confirmed."""
        try:
            donate_request = vtb_payment.donate_request
        except DonateRequest.DoesNotExist:
            self.stderr.write(
                f"No DonateRequest for {vtb_payment.order_id}, skipping donation update"
            )
            return

        period = DonationPeriod.objects.filter(name=donate_request.comment).first()
        if period is None:
            self.stderr.write(
                f"DonationPeriod not found for comment {donate_request.comment!r} "
                f"(order {vtb_payment.order_id}), skipping donation update"
            )
            return

        member, member_created = ClubMember.objects.get_or_create(
            name=donate_request.sender_name
        )
        if member_created:
            self.stdout.write(
                f"ClubMember created: {donate_request.sender_name!r} "
                f"(order {vtb_payment.order_id})"
            )

        paid_date = (
            vtb_payment.created_at.date()
            if vtb_payment.created_at
            else timezone.now().date()
        )
        donation, created = MemberDonation.objects.get_or_create(
            member=member,
            period=period,
            defaults={
                "is_paid": True,
                "amount": vtb_payment.amount_value,
                "paid_date": paid_date,
                "recipient": MemberDonation.RECIPIENT_SBP,
            },
        )
        if not created and not donation.is_paid:
            donation.is_paid = True
            donation.amount = vtb_payment.amount_value
            donation.paid_date = paid_date
            donation.recipient = MemberDonation.RECIPIENT_SBP
            donation.save(update_fields=["is_paid", "amount", "paid_date", "recipient"])

        action = "created" if created else "updated"
        self.stdout.write(
            f"MemberDonation {action}: {member} / {period} "
            f"(order {vtb_payment.order_id})"
        )
