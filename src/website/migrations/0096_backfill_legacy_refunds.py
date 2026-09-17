"""Строки возвратов для платежей, откаченных до появления ``PaymentRefund``.

У таких платежей есть только статус ``cancel``: деньги и места с команды сняты,
но ни даты возврата, ни ``refundId`` банка не сохранилось. Без этих строк они
выпали бы из «Возвращено» на странице платежей, и брутто поехало бы, а
настоящий ``refundId``, пришедший позже по тому же заказу, списал бы деньги
второй раз — остаток платежа выглядел бы нетронутым.

``refunded_at`` берём из ``updated_at`` платежа — лучшего приближения нет.

Отдельной миграцией, а не вместе со схемой: если backfill упадёт на данных,
таблица уже создана и чинить нужно только данные.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Payment = apps.get_model("website", "Payment")
    PaymentRefund = apps.get_model("website", "PaymentRefund")
    rows = [
        PaymentRefund(
            payment=payment,
            vtb_refund_id=f"LEGACY_{payment.pk}",
            amount=payment.payment_amount,
            people=payment.paid_for,
            refunded_at=payment.updated_at,
            status="LEGACY",
        )
        for payment in Payment.objects.filter(status="cancel")
    ]
    PaymentRefund.objects.bulk_create(rows, ignore_conflicts=True)


def drop(apps, schema_editor):
    PaymentRefund = apps.get_model("website", "PaymentRefund")
    PaymentRefund.objects.filter(status="LEGACY").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0095_paymentrefund"),
    ]

    operations = [migrations.RunPython(backfill, drop)]
