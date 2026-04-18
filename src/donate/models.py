from django.db import models

from website.models import VTBPayment

PAID_STATUSES = {"PAID", "CONFIRMED"}


class DonateRequest(models.Model):
    payment = models.OneToOneField(
        VTBPayment,
        on_delete=models.CASCADE,
        related_name="donate_request",
    )
    sender_name = models.CharField(max_length=120)
    comment = models.CharField(max_length=255)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created",)

    def __str__(self):
        return f"{self.sender_name} ({self.comment})"


class ClubMember(models.Model):
    name = models.CharField(max_length=120, unique=True)
    notes = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class DonationPeriod(models.Model):
    name = models.CharField(max_length=120, unique=True)
    date = models.DateField(
        help_text="Дата начала периода — используется для сортировки"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Показывать в таблице на сайте",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date",)

    def __str__(self):
        return self.name


class MemberDonation(models.Model):
    RECIPIENT_TAMILA = "tamila"
    RECIPIENT_SBP = "sbp"
    RECIPIENT_CHOICES = [
        (RECIPIENT_TAMILA, "Тамиле"),
        (RECIPIENT_SBP, "СБП через сайт"),
    ]

    member = models.ForeignKey(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="donations",
    )
    period = models.ForeignKey(
        DonationPeriod,
        on_delete=models.CASCADE,
        related_name="member_donations",
    )
    is_paid = models.BooleanField(default=False)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True, verbose_name="Дата взноса")
    recipient = models.CharField(
        max_length=20,
        choices=RECIPIENT_CHOICES,
        blank=True,
        verbose_name="Кому",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("member", "period")

    def __str__(self):
        status = "оплатил" if self.is_paid else "не оплатил"
        return f"{self.member} — {self.period} ({status})"
