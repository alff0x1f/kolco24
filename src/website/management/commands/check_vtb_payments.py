from decimal import Decimal

from django.core.management.base import BaseCommand

from vtb.client import VTBClient
from website.models import Payment, VTBPayment


class Command(BaseCommand):
    help = (
        "Check VTB payments status and update related Payment and Team if paid."
    )

    def handle(self, *args, **options):
        client = VTBClient()
        payments = VTBPayment.objects.exclude(status__iexact="PAID")
        for vtb_payment in payments:
            try:
                payload = client.get_order(vtb_payment.order_id)
            except Exception as exc:  # pragma: no cover - network/HTTP failures
                self.stderr.write(
                    f"Failed to fetch order {vtb_payment.order_id}: {exc}"
                )
                continue
            # Reuse existing parser
            vtb_payment = VTBPayment.from_vtb_payload({"object": payload})
            if vtb_payment.status.upper() != "PAID":
                continue
            # order_id has format ORDER_<payment_id>
            try:
                payment_id = int(vtb_payment.order_id.split("_")[-1])
            except (ValueError, AttributeError):
                continue
            payment = Payment.objects.filter(pk=payment_id).first()
            if not payment or payment.status == Payment.STATUS_DONE:
                continue
            team = payment.team
            if team:
                team.paid_people += payment.paid_for
                team.paid_sum += payment.payment_amount
                team.save(update_fields=["paid_people", "paid_sum"])
            payment.status = Payment.STATUS_DONE
            payment.order = payment.pk
            payment.balance = Decimal("0")
            payment.save(update_fields=["status", "order", "balance"])
            self.stdout.write(f"Payment {payment.pk} marked as paid")
