from django.db import models


class SbpPaymentRecipient(models.Model):
    bank = models.CharField("Название банка", max_length=50)
    name = models.CharField("Получатель", max_length=100)
    phone = models.CharField("Телефон", max_length=20)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.bank})"

    class Meta:
        verbose_name = "Получатель платежа"
        verbose_name_plural = "Получатели платежей"
