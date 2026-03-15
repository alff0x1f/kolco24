from django.db import models

from website.models import VTBPayment


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
