from decimal import Decimal

from django.db import models, transaction
from django.utils.dateparse import parse_datetime


class VTBPayment(models.Model):
    # Your own business key when you request the order
    order_id = models.CharField(max_length=64, unique=True, db_index=True)

    # VTB-generated code used in payUrl; keep unique for idempotency
    order_code = models.CharField(max_length=64, unique=True, null=True, blank=True)

    # Money
    amount_value = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="RUB")

    # Lifecycle timestamps from VTB (UTC)
    created_at = models.DateTimeField(null=True, blank=True)
    expire_at = models.DateTimeField(null=True, blank=True)

    # Status from VTB
    status = models.CharField(max_length=32, db_index=True)  # e.g. "CREATED"
    status_description = models.CharField(max_length=128, blank=True)  # e.g. "CREATED"
    status_changed_at = models.DateTimeField(null=True, blank=True)

    # Where payer is redirected / QR page
    pay_url = models.URLField(max_length=500, blank=True)

    # Keep the original payload for audit/debug
    raw = models.JSONField(default=dict, blank=True)

    # Local bookkeeping
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created",)

    def __str__(self):
        return f"VTB Payment {self.order_id} ({self.status})"

    @classmethod
    @transaction.atomic
    def from_vtb_payload(cls, payload: dict) -> "VTBPayment":
        """
        Upsert Payment + its PreparedPayments from VTB 'create_order' response.
        Safe to call repeatedly (idempotent by order_id).
        """
        obj = payload.get("object", {}) or {}

        amount = obj.get("amount", {}) or {}
        status = obj.get("status", {}) or {}

        # Parse datetimes (handles trailing 'Z' as UTC)
        created_at = parse_datetime(obj.get("createdAt") or "")
        expire_at = parse_datetime(obj.get("expire") or "")
        status_changed_at = parse_datetime(status.get("changedAt") or "")

        # Coerce decimals safely
        value = amount.get("value")
        amount_value = Decimal(str(value)) if value is not None else Decimal("0.00")

        payment, _created = cls.objects.update_or_create(
            order_id=obj.get("orderId"),
            defaults={
                "order_code": obj.get("orderCode"),
                "amount_value": amount_value,
                "currency": amount.get("code") or "RUB",
                "created_at": created_at,
                "expire_at": expire_at,
                "status": status.get("value") or "",
                "status_description": status.get("description") or "",
                "status_changed_at": status_changed_at,
                "pay_url": obj.get("payUrl") or "",
                "raw": payload,
            },
        )

        # Refresh prepared payments (small list, simplest is replace)
        payment.prepared_payments.all().delete()
        for pp in obj.get("preparedPayments", []) or []:
            VTBPreparedPayment.from_vtb_item(payment, pp)

        return payment


class VTBPreparedPayment(models.Model):
    payment = models.ForeignKey(
        VTBPayment,
        on_delete=models.CASCADE,
        related_name="prepared_payments",
    )
    type = models.CharField(max_length=32)  # e.g. "sbp"
    qrc_id = models.CharField(max_length=128, blank=True)
    url = models.URLField(max_length=500, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.type} for {self.payment.order_id}"

    @classmethod
    def from_vtb_item(cls, payment: VTBPayment, item: dict) -> "VTBPreparedPayment":
        typ = (item or {}).get("type") or ""
        obj = (item or {}).get("object", {}) or {}
        return cls.objects.create(
            payment=payment,
            type=typ,
            qrc_id=obj.get("qrcId") or "",
            url=obj.get("url") or "",
            raw=item or {},
        )
