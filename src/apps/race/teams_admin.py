"""Список команд гонки для организатора: места, переносы и возвраты.

Единственный источник строк и для страницы, и для CSV-экспорта.
"""

from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from website.models.models import PaymentRefund, Team, TeamMemberMove

from .finance import _csv_safe

_DATE_FORMAT = "%d.%m.%Y %H:%M"


def start_number_key(team):
    number = team.start_number.strip()
    if number.isdecimal():
        return (0, int(number), "", team.id)
    return (1, 0, number, team.id)


def _format_date(moment):
    return timezone.localtime(moment).strftime(_DATE_FORMAT) if moment else ""


def _team_label(team):
    if team.teamname:
        return f"ID-{team.id} «{team.teamname}»"
    return f"ID-{team.id}"


def _moves_by_team(race):
    # Удалённая команда остаётся видна как вторая сторона переноса: связанные
    # объекты Django грузит базовым менеджером, мимо фильтра ``is_deleted``.
    moves = (
        TeamMemberMove.objects.filter(
            Q(from_team__category2__race=race) | Q(to_team__category2__race=race)
        )
        .select_related("from_team", "to_team")
        .order_by("move_date", "id")
    )
    by_team = defaultdict(list)
    for move in moves:
        for team_id, people, other in (
            (move.from_team_id, -move.moved_people, move.to_team),
            (move.to_team_id, move.moved_people, move.from_team),
        ):
            by_team[team_id].append(
                {
                    "date": _format_date(move.move_date),
                    "people": people,
                    "other": _team_label(other),
                }
            )
    return by_team


def _refunds_by_team(race):
    # Нулевая строка — только аудит банка, ни денег, ни мест она не двигала.
    refunds = (
        PaymentRefund.objects.filter(payment__team__category2__race=race)
        .exclude(amount=0)
        .select_related("payment")
    )
    by_team = defaultdict(list)
    for refund in refunds:
        moment = refund.refunded_at or refund.created_at
        by_team[refund.payment.team_id].append(
            {
                "moment": moment,
                "date": _format_date(moment),
                "people": refund.people,
                "amount": refund.amount,
            }
        )
    for items in by_team.values():
        items.sort(key=lambda item: item["moment"])
    return by_team


def team_rows(race, category=None):
    """Строка на команду: оплаченные места, переносы и возвраты.

    Кроме команд с оплаченными местами попадают и те, у кого места ушли
    целиком — переносом или возвратом, иначе их история бы потерялась.
    Удалённые команды строк не получают.
    """
    moves = _moves_by_team(race)
    refunds = _refunds_by_team(race)
    teams = Team.objects.filter(category2__race=race).select_related(
        "owner", "category2"
    )
    if category is not None:
        teams = teams.filter(category2=category)

    rows = []
    for team in sorted(teams, key=start_number_key):
        team_moves = moves.get(team.id, [])
        team_refunds = refunds.get(team.id, [])
        if not (team.paid_people > 0 or team_moves or team_refunds):
            continue
        rows.append(
            {
                "id": team.id,
                "number": team.start_number,
                "name": team.teamname
                or f"Без названия ({team.owner.last_name} {team.owner.first_name})",
                "category": team.category2.code,
                "paid_people": team.paid_people,
                "ucount": team.ucount,
                "moves": team_moves,
                "moved_people": sum(move["people"] for move in team_moves),
                "refunds": team_refunds,
                "refunded_people": sum(item["people"] for item in team_refunds),
                "refunded_amount": sum(item["amount"] for item in team_refunds),
                "last_refund_date": team_refunds[-1]["date"] if team_refunds else "",
            }
        )
    return rows


def _number(value):
    return int(value) if float(value).is_integer() else value


def _when(count, last_date):
    if count > 1:
        return f"({count} шт., посл. {last_date})"
    return f"({last_date})"


def move_text(move):
    # Дата впереди: ячейка, начатая с «+» или «-», получила бы апостроф
    # от ``_csv_safe``.
    direction = "из" if move["people"] >= 0 else "в"
    return f"{move['date']} {direction} {move['other']}: {move['people']:+g}"


def refund_summary_text(row):
    if not row["refunds"]:
        return ""
    return (
        f"{row['refunded_people']:g} чел., {row['refunded_amount']:g} ₽ "
        f"{_when(len(row['refunds']), row['last_refund_date'])}"
    )


def csv_rows(rows):
    yield [
        "ID",
        "Старт номер",
        "Название команды",
        "Категория",
        "Оплачено чел",
        "Заявлено чел",
        "Переносы чел",
        "Переносы",
        "Возвращено чел",
        "Возвращено ₽",
        "Последний возврат",
        "Возвраты",
    ]
    for row in rows:
        yield [
            row["id"],
            _csv_safe(row["number"]),
            _csv_safe(row["name"]),
            _csv_safe(row["category"]),
            _number(row["paid_people"]),
            row["ucount"],
            _number(row["moved_people"]),
            _csv_safe("; ".join(move_text(move) for move in row["moves"])),
            _number(row["refunded_people"]),
            _number(row["refunded_amount"]),
            row["last_refund_date"],
            _csv_safe(refund_summary_text(row)),
        ]
