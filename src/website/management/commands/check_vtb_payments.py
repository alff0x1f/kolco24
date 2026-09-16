from datetime import timedelta
from decimal import Decimal
from time import sleep

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.race.settlement import credit_extras, refund_payment, settle_payment
from donate.models import ClubMember, DonateRequest, DonationPeriod, MemberDonation
from vtb.client import VTBClient
from website.models import Payment, Team, VTBPayment


class Command(BaseCommand):
    help = "Check VTB payments status and update related Payment and Team if paid."
    donate_prefix = "SPUTNIK"

    fresh_age = timedelta(minutes=10)
    stale_check_interval = timedelta(minutes=3)

    def add_arguments(self, parser):
        parser.add_argument(
            "--order-id",
            action="append",
            dest="order_ids",
            default=[],
            help=(
                "Check these orders once and exit, whatever their local status. "
                "The only way to notice a refund: a PAID order is never polled "
                "again by the endless loop."
            ),
        )

    def handle(self, *args, **options):
        client = VTBClient()
        order_ids = options.get("order_ids") or []
        if order_ids:
            self._check_once(client, order_ids)
            return
        last_stale_check = None
        while True:
            now = timezone.now()
            payments = (
                VTBPayment.objects.exclude(status__iexact="PAID")
                .exclude(status__iexact="EXPIRED")
                .exclude(status__iexact="REFUNDED")
            )
            # Orders older than fresh_age are rechecked less often, but never
            # dropped: one paid just before expiry still has to be settled.
            fresh_only = (
                last_stale_check is not None
                and now - last_stale_check < self.stale_check_interval
            )
            if fresh_only:
                payments = payments.filter(created__gte=now - self.fresh_age)
            else:
                last_stale_check = now
            if not payments:
                scope = "fresh " if fresh_only else ""
                self.stdout.write(f"No pending {scope}VTB payments found, sleeping...")
            for vtb_payment in payments:
                self._check_payment(client, vtb_payment)
                sleep(1)  # avoid hitting rate limits
            sleep(10)

    def _check_once(self, client: VTBClient, order_ids: list) -> None:
        for order_id in order_ids:
            vtb_payment = VTBPayment.objects.filter(order_id=order_id).first()
            if vtb_payment is None:
                self.stderr.write(f"No VTBPayment with order_id={order_id!r}")
                continue
            self._check_payment(client, vtb_payment)

    def _check_payment(self, client: VTBClient, vtb_payment: VTBPayment) -> None:
        self.stdout.write(f"Checking VTB payment {vtb_payment.pk}...")

        try:
            payload = client.get_order(vtb_payment.order_id)
        except Exception as exc:  # pragma: no cover - network/HTTP failures
            self.stderr.write(f"Failed to fetch order {vtb_payment.order_id}: {exc}")
            return
        obj = payload.get("object", {}) or {}
        new_status = (obj.get("status", {}) or {}).get("value", "")
        if new_status.upper() == "EXPIRED":
            self._store_status(vtb_payment, payload)
            self.stdout.write(f"Payment {vtb_payment.pk} marked as expired")
            return

        if new_status.upper() == "PAID":
            self._store_status(vtb_payment, payload)
            self.stdout.write(f"VTBPayment {vtb_payment.pk} status→PAID")

            if vtb_payment.order_id.startswith(f"{self.donate_prefix}_"):
                self._process_donation(vtb_payment)
                return

            payment = self._resolve_race_payment(vtb_payment)
            if self._settle_race_payment(payment):
                self.stdout.write(f"Payment {payment.pk} marked as paid")
            return

        if new_status.upper() == "REFUNDED":
            self._process_refund(vtb_payment, payload)

    def _store_status(self, vtb_payment: VTBPayment, payload: dict) -> None:
        status = (payload.get("object", {}) or {}).get("status", {}) or {}
        vtb_payment.status = status.get("value", "")
        vtb_payment.status_description = status.get("description", "")
        vtb_payment.save(update_fields=["status", "status_description"])

    def _process_refund(self, vtb_payment: VTBPayment, payload: dict) -> None:
        """Take a refunded order's money back out of the team it was credited to.

        A refund only ever shows up on an order that was already ``PAID``, and
        such an order is excluded from the polling loop — so this normally runs
        from ``--order-id``, started by hand after a refund is made.
        """
        refunded = self._refunded_amount(payload)
        if refunded < Decimal(str(vtb_payment.amount_value)):
            self.stderr.write(
                f"Order {vtb_payment.order_id} is REFUNDED for {refunded} of "
                f"{vtb_payment.amount_value}; partial refunds are not handled, "
                f"fix the team by hand"
            )
            return
        self._store_status(vtb_payment, payload)

        if vtb_payment.order_id.startswith(f"{self.donate_prefix}_"):
            self._refund_donation(vtb_payment)
            return

        payment = self._resolve_race_payment(vtb_payment)
        if refund_payment(payment):
            self.stdout.write(f"Payment {payment.pk} rolled back after refund")
        else:
            self.stderr.write(
                f"Order {vtb_payment.order_id} is REFUNDED but its Payment is "
                f"not 'done' — nothing to roll back"
            )

    @staticmethod
    def _refunded_amount(payload: dict) -> Decimal:
        refunds = ((payload.get("object", {}) or {}).get("transactions", {}) or {}).get(
            "refunds", []
        ) or []
        total = Decimal("0")
        for refund in refunds:
            obj = (refund or {}).get("object", {}) or {}
            status = (obj.get("status", {}) or {}).get("value", "")
            if status.upper() not in ("RECONCILED", "REFUNDED", "COMPLETED"):
                continue
            value = (obj.get("amount", {}) or {}).get("value")
            if value is not None:
                total += Decimal(str(value))
        return total

    def _refund_donation(self, vtb_payment: VTBPayment) -> None:
        """Mark a refunded donation unpaid; the mirror of ``_process_donation``."""
        try:
            donate_request = vtb_payment.donate_request
        except DonateRequest.DoesNotExist:
            self.stderr.write(
                f"No DonateRequest for {vtb_payment.order_id}, skipping refund"
            )
            return
        period = DonationPeriod.objects.filter(name=donate_request.comment).first()
        member = ClubMember.objects.filter(name=donate_request.sender_name).first()
        if period is None or member is None:
            self.stderr.write(
                f"No MemberDonation to undo for {vtb_payment.order_id}, "
                f"skipping refund"
            )
            return
        updated = MemberDonation.objects.filter(
            member=member, period=period, is_paid=True
        ).update(is_paid=False)
        if updated:
            self.stdout.write(
                f"MemberDonation marked unpaid: {member} / {period} "
                f"(order {vtb_payment.order_id})"
            )

    def _settle_race_payment(self, payment) -> bool:
        """Credit a confirmed race Payment exactly once.

        Thin wrapper over ``apps.race.settlement.settle_payment``, which the
        zero-charge promo path in ``apps/race/pricing.py`` also calls.
        """
        return settle_payment(payment)

    def _credit_extras(self, team: Team, payment: Payment) -> None:
        """Thin wrapper over ``apps.race.settlement.credit_extras``."""
        credit_extras(team, payment)

    def _resolve_race_payment(self, vtb_payment: VTBPayment):
        # New ORDER_<ulid> payments link via the explicit FK.
        try:
            return vtb_payment.race_payment
        except Payment.DoesNotExist:
            pass
        # Legacy fallback: order_id == "ORDER_<payment_id>".
        if not vtb_payment.order_id.startswith("ORDER_"):
            return None
        try:
            payment_id = int(vtb_payment.order_id.split("_")[-1])
        except ValueError:
            self.stderr.write(
                f"Cannot resolve race payment for order_id={vtb_payment.order_id!r} "
                f"(no FK and non-integer suffix)"
            )
            return None
        return Payment.objects.filter(pk=payment_id).first()

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
