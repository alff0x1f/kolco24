"""Данные для админской страницы платежей гонки.

Единственный источник строк и для страницы, и для CSV-экспорта, чтобы цифры
в браузере и в выгрузке не могли разойтись.
"""

from django.utils import timezone

from website.models.models import Payment

# Значения фильтра статуса (query string ``?status=``).
STATUS_DONE = "done"
STATUS_UNPAID = "unpaid"
STATUS_CANCEL = "cancel"
STATUS_ALL = "all"

_STATUS_LABELS = {
    STATUS_DONE: "Оплачено",
    STATUS_UNPAID: "Не оплачено",
    STATUS_CANCEL: "Возврат",
}

_NO_NAME = "без названия"


def _row_status(payment):
    """``draft`` и ``draft_with_info`` неразличимы для финанализа — сливаем."""
    if payment.status == Payment.STATUS_DONE:
        return STATUS_DONE
    if payment.status == Payment.STATUS_CANCEL:
        return STATUS_CANCEL
    return STATUS_UNPAID


def _paid_moment(payment):
    """Момент, которым платёж попадает в отчётность.

    Для оплаченного это время, когда банк сообщил об оплате
    (``VTBPayment.status_changed_at``); ``updated_at`` — фолбэк для платежей без
    VTB-заказа или без отметки статуса. Для остальных — момент создания.
    ``Payment.payment_date`` в коде нигде не заполняется, поэтому не читается.
    """
    if payment.status == Payment.STATUS_DONE:
        vtb = payment.vtb_payment
        if vtb is not None and vtb.status_changed_at:
            return vtb.status_changed_at
        return payment.updated_at
    return payment.created_at


def _extras_of(payment):
    """Снапшоты доп-услуг платежа: количества, деньги по каждой, подпись.

    Деньги считаются по каждой услуге отдельно (``unit_price`` — снапшот на
    момент оплаты), чтобы разбивка дохода не восстанавливала их из общей суммы.
    """
    counts = {}
    money = {}
    labels = []
    for pe in payment.extras.all():
        if not pe.count:
            continue
        code = pe.race_extra.code
        counts[code] = counts.get(code, 0) + pe.count
        money[code] = money.get(code, 0) + pe.count * pe.unit_price
        labels.append(f"{pe.race_extra.name} ×{pe.count}")
    return counts, money, ", ".join(labels)


def payments_queryset(race):
    """Платежи гонки. Связь с гонкой — только через ``team.category2.race``.

    Платежи без команды привязать к гонке нечем — они в срез не попадают.
    Платежи удалённых команд (``Team.is_deleted``) попадают намеренно:
    ``Payment.objects`` не проходит через ``TeamManager``, а деньги удалённой
    команды — реально полученные деньги.
    """
    return (
        Payment.objects.filter(team__category2__race=race)
        .select_related("team", "team__category2", "promo", "vtb_payment")
        .prefetch_related("extras__race_extra")
        .order_by("-created_at")
    )


def payment_rows(race):
    """Плоские строки реестра платежей гонки.

    Все даты — уже отформатированные строки в таймзоне проекта: строки уходят в
    JSON-остров через ``_safe_json`` (голый ``json.dumps`` без ``default=``),
    который не умеет сериализовать ``datetime``. Группировка по дням тоже
    считается здесь, а не в JS: у браузера своя таймзона, и дневные суммы на
    странице разошлись бы с CSV.
    """
    rows = []
    for payment in payments_queryset(race):
        moment = _paid_moment(payment)
        local = timezone.localtime(moment) if moment else None
        counts, extras_money, extras_label = _extras_of(payment)
        extras_sum = sum(extras_money.values())
        status = _row_status(payment)
        team = payment.team
        # Участие считается остатком, а не ``paid_for × cost_per_person``: при
        # промокоде и доп-услугах ``payment_amount`` намеренно расходится с этим
        # произведением (см. ``create_team_payment``), и только остаток даёт
        # сходимость разбивки с итогом на каждой строке.
        amount = round(payment.payment_amount, 2)
        discount = payment.discount_amount
        rows.append(
            {
                "id": payment.id,
                "paid_at": local.strftime("%d.%m.%y %H:%M") if local else "",
                "paid_sort": local.strftime("%Y-%m-%dT%H:%M") if local else "",
                "paid_date": local.strftime("%Y-%m-%d") if local else "",
                "team_id": team.id,
                "team_name": team.teamname or _NO_NAME,
                "category": team.category2.code if team.category2 else "",
                "status": status,
                "status_label": _STATUS_LABELS[status],
                "paid_for": payment.paid_for,
                "cost_per_person": payment.cost_per_person,
                "promo": payment.promo.code if payment.promo else "",
                "discount": discount,
                "amount": amount,
                "order_id": payment.vtb_payment.order_id if payment.vtb_payment else "",
                "extras": counts,
                "extras_money": extras_money,
                "extras_sum": extras_sum,
                "extras_label": extras_label,
                "fee_sum": round(amount + discount - extras_sum, 2),
            }
        )
    return rows


def filter_rows(rows, status):
    """Отфильтровать строки по статусу — только для CSV.

    Страница отдаёт все строки и фильтрует их в браузере; сюда приходит значение
    из query string, поэтому неизвестное значение молча трактуется как ``done``.
    """
    if status == STATUS_ALL:
        return list(rows)
    if status not in (STATUS_UNPAID, STATUS_CANCEL):
        status = STATUS_DONE
    return [row for row in rows if row["status"] == status]


# Excel и LibreOffice вычисляют ячейку, начинающуюся с любого из этих символов.
_CSV_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Обезвредить формулу в текстовой ячейке.

    Название команды пишет участник, поэтому «=1+1» в выгрузке стало бы
    вычисляемой ячейкой: ``csv.writer`` экранирует структуру файла, но не
    защищает от интерпретации формул. Апостроф в начале заставляет Excel
    показать значение как текст. Числа проходят мимо — их формирует сервер, и
    отрицательные суммы не должны получить апостроф.
    """
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_STARTS):
        return "'" + value
    return value


def csv_rows(rows, extras):
    """Заголовок и строки CSV: по колонке на каждую услугу каталога."""
    header = [
        "Дата",
        "Команда",
        "Категория",
        "Статус",
        "Участников",
        "Цена за человека",
        "Промокод",
        "Скидка",
        "Взнос",
        "Сумма",
        "Заказ",
    ]
    header += [_csv_safe(extra["name"]) for extra in extras]
    yield header
    for row in rows:
        line = [
            _csv_safe(value)
            for value in (
                row["paid_at"],
                row["team_name"],
                row["category"],
                row["status_label"],
                row["paid_for"],
                row["cost_per_person"],
                row["promo"],
                row["discount"],
                row["fee_sum"],
                row["amount"],
                row["order_id"],
            )
        ]
        line += [row["extras"].get(extra["code"], 0) for extra in extras]
        yield line


def extras_catalog(race):
    """Каталог доп-услуг гонки в порядке отображения.

    Неактивные услуги тоже здесь: по ним могли быть продажи, и строка разбивки
    должна остаться. Отсюда же берутся колонки CSV.
    """
    return [{"code": extra.code, "name": extra.name} for extra in race.extras.all()]
