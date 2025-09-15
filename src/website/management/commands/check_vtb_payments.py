from time import sleep

from django.core.management.base import BaseCommand
from website.models import Payment, Team, VTBPayment

from vtb.client import VTBClient


class Command(BaseCommand):
    help = "Check VTB payments status and update related Payment and Team if paid."

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
