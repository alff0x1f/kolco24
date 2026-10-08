import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.race import finish_forecast as ff
from apps.race.test_starts import _admin
from apps.race.tests import _make_category, _make_race, _make_team, _script_json
from website.models.race import RaceAdmin

MIN = ff.MINUTE_MS


def _at(hh, mm=0, ss=0, day=9):
    """Milliseconds of a local (project ``TIME_ZONE``) wall-clock moment."""
    moment = datetime.datetime(2026, 10, day, hh, mm, ss)
    return int(timezone.make_aware(moment).timestamp() * 1000)


def _labels(rows):
    return [(r["from"], r["to"]) for r in rows]


# --- team_state ---------------------------------------------------------------


@pytest.mark.parametrize(
    "late_ms, state, long",
    [
        (-MIN, ff.ON_COURSE, False),
        (0, ff.OVERDUE, False),
        (59 * MIN + 59_000, ff.OVERDUE, False),
        (60 * MIN, ff.OVERDUE, False),
        (60 * MIN + 1, ff.OVERDUE, True),
    ],
)
def test_team_state_around_deadline(late_ms, state, long):
    start = _at(9)
    now = start + 720 * MIN + late_ms
    assert ff.team_state(start, None, 720, now) == (state, long)


def test_team_state_finish_wins_over_overdue():
    start = _at(9)
    now = start + 900 * MIN
    assert ff.team_state(start, now, 720, now) == (ff.FINISHED, False)


def test_team_state_unusable_finish_is_not_finished():
    start = _at(9)
    assert ff.team_state(start, None, 720, start + MIN)[0] == ff.ON_COURSE


def test_team_state_not_started_and_no_control():
    assert ff.team_state(None, None, 720, _at(9)) == (ff.NOT_STARTED, False)
    assert ff.team_state(_at(9), None, 0, _at(10)) == (ff.NO_CONTROL, False)


# --- hour_buckets -------------------------------------------------------------


def test_hour_buckets_first_is_partial():
    buckets = ff.hour_buckets(_at(15, 20), [_at(17, 30)])
    assert buckets == [
        (_at(15, 20), _at(16)),
        (_at(16), _at(17)),
        (_at(17), _at(18)),
    ]


def test_hour_buckets_now_on_the_hour_gives_full_first_hour():
    assert ff.hour_buckets(_at(15), [_at(16, 30)]) == [
        (_at(15), _at(16)),
        (_at(16), _at(17)),
    ]


def test_hour_buckets_deadline_on_the_hour_adds_no_empty_hour():
    assert ff.hour_buckets(_at(15, 20), [_at(17)])[-1] == (_at(16), _at(17))


def test_hour_buckets_only_past_deadlines_keep_one_bucket():
    assert ff.hour_buckets(_at(15, 20), [_at(15)]) == [(_at(15, 20), _at(16))]


def test_hour_buckets_empty():
    assert ff.hour_buckets(_at(15, 20), []) == []


# --- spread_team --------------------------------------------------------------


@pytest.mark.parametrize("elapsed_min", [0, 300, 600, 690, 719])
def test_spread_adds_up_to_people(elapsed_min):
    now = _at(15, 20)
    start = now - elapsed_min * MIN
    buckets = ff.hour_buckets(now, [ff.deadline_ms(start, 720)])
    shares = ff.spread_team(start, 720, 4, now, buckets)
    assert sum(shares) == pytest.approx(4)


def test_spread_one_minute_before_deadline_goes_to_deadline_bucket():
    now = _at(15, 20)
    start = now - 719 * MIN
    buckets = ff.hour_buckets(now, [ff.deadline_ms(start, 720)])
    shares = ff.spread_team(start, 720, 3, now, buckets)
    assert shares[0] == pytest.approx(3)


def test_spread_degenerate_window_puts_all_in_deadline_bucket(monkeypatch):
    monkeypatch.setattr(ff, "_MIN_WINDOW_MASS", 2.0)
    now = _at(15, 20)
    start = now - 600 * MIN
    buckets = ff.hour_buckets(now, [ff.deadline_ms(start, 720)])
    assert ff.spread_team(start, 720, 3, now, buckets) == [0.0, 0.0, 3.0]
    late = [(_at(15, 20), _at(16))]
    assert ff.spread_team(now - 720 * MIN, 720, 3, now, late) == [3.0]


def test_spread_fresh_12h_start_lands_in_last_two_hours():
    now = start = _at(9)
    buckets = ff.hour_buckets(now, [ff.deadline_ms(start, 720)])
    assert buckets[-1] == (_at(20), _at(21))
    shares = ff.spread_team(start, 720, 4, now, buckets)
    assert sum(shares[-2:]) > 0.95 * 4


def test_spread_future_start_puts_nothing_before_start():
    now = _at(9, 20)
    start = _at(9, 50)
    deadline = ff.deadline_ms(start, 720)
    buckets = [(now, start), (start, deadline)]
    shares = ff.spread_team(start, 720, 2, now, buckets)
    assert shares[0] == 0
    assert shares[1] == pytest.approx(2)


# --- build_forecast -----------------------------------------------------------


def _team(category_id, people, start_ms, now_ms, control_min=720):
    state, overdue_long = ff.team_state(start_ms, None, control_min, now_ms)
    return {
        "category_id": category_id,
        "people": people,
        "start_ms": start_ms,
        "control_min": control_min,
        "state": state,
        "overdue_long": overdue_long,
    }


def test_forecast_overdue_within_grace_goes_to_first_bucket():
    now = _at(15, 20)
    teams = [_team(1, 3, now - 750 * MIN, now)]
    forecast = ff.build_forecast(teams, [1], now)
    assert forecast["all"] == [{"from": "15:20", "to": "16:00", "people": 3.0}]
    assert forecast["1"] == forecast["all"]


def test_forecast_skips_long_overdue_no_control_and_not_started():
    now = _at(15, 20)
    teams = [
        _team(1, 3, now - 800 * MIN, now),
        _team(1, 2, now - 60 * MIN, now, control_min=0),
        _team(1, 5, None, now),
    ]
    assert ff.build_forecast(teams, [1, 2], now) == {"all": [], "1": [], "2": []}


def test_forecast_every_category_shares_one_grid():
    now = _at(15, 20)
    teams = [
        _team(1, 4, now - 600 * MIN, now),
        _team(2, 2, now - 100 * MIN, now, control_min=360),
    ]
    forecast = ff.build_forecast(teams, [1, 2, 3], now)
    assert set(forecast) == {"all", "1", "2", "3"}
    grid = _labels(forecast["all"])
    assert grid[0] == ("15:20", "16:00")
    for key in ("1", "2", "3"):
        assert _labels(forecast[key]) == grid
    assert all(r["people"] == 0 for r in forecast["3"])
    total = sum(r["people"] for r in forecast["all"])
    assert total == pytest.approx(6, abs=0.05 * len(grid))


# --- build_timeline -----------------------------------------------------------


def _finished(category_id, people, finish_ms):
    return {
        "category_id": category_id,
        "people": people,
        "start_ms": finish_ms - 600 * MIN,
        "control_min": 720,
        "state": ff.FINISHED,
        "overdue_long": False,
        "finish_ms": finish_ms,
    }


def _hours(rows):
    return [(r["from"], r["to"]) for r in rows]


def test_timeline_starts_at_first_finish_hour():
    now = _at(15, 20)
    teams = [_finished(1, 2, _at(13, 10)), _finished(1, 3, _at(15, 5))]
    rows = ff.build_timeline(teams, [1], now)["all"]
    assert _hours(rows) == [("13:00", "14:00"), ("14:00", "15:00"), ("15:00", "16:00")]
    assert [r["arrived"] for r in rows] == [2, 0, 3]
    assert [r["expected"] for r in rows] == [None, None, 0.0]
    assert [r["now"] for r in rows] == [False, False, True]


def test_timeline_finish_on_the_hour_belongs_to_previous_hour():
    now = _at(15, 20)
    rows = ff.build_timeline([_finished(1, 2, _at(15))], [1], now)["all"]
    assert rows[0] == {
        "from": "14:00",
        "to": "15:00",
        "arrived": 2,
        "expected": None,
        "now": False,
    }
    assert rows[1]["arrived"] == 0


def test_timeline_future_finish_goes_to_current_hour():
    now = _at(15, 20)
    rows = ff.build_timeline([_finished(1, 2, _at(18))], [1], now)["all"]
    assert rows == [
        {"from": "15:00", "to": "16:00", "arrived": 2, "expected": 0.0, "now": True}
    ]


def test_timeline_now_on_the_hour():
    now = _at(15)
    teams = [_finished(1, 2, now), _finished(1, 3, now + 1)]
    rows = ff.build_timeline(teams, [1], now)["all"]
    assert _hours(rows) == [("14:00", "15:00"), ("15:00", "16:00")]
    assert [r["arrived"] for r in rows] == [2, 3]


def test_timeline_finish_exactly_now_is_current_hour():
    now = _at(15, 20)
    rows = ff.build_timeline([_finished(1, 2, now)], [1], now)["all"]
    assert rows[-1]["now"] and rows[-1]["arrived"] == 2


def test_timeline_counts_finish_without_control_time():
    now = _at(15, 20)
    team = _finished(2, 4, _at(14, 30))
    team["control_min"] = 0
    timeline = ff.build_timeline([team], [1, 2], now)
    assert timeline["all"][0]["arrived"] == 4
    assert timeline["2"][0]["arrived"] == 4
    assert timeline["1"][0]["arrived"] == 0
    assert _hours(timeline["1"]) == _hours(timeline["all"])


def test_timeline_merges_forecast_by_position():
    now = _at(15, 20)
    teams = [
        _finished(1, 2, _at(14, 30)),
        _team(1, 3, now - 750 * MIN, now),
        _team(1, 4, now - 600 * MIN, now),
    ]
    rows = ff.build_timeline(teams, [1], now)["all"]
    forecast = ff.build_forecast(teams, [1], now)["all"]
    assert rows[0]["expected"] is None and rows[0]["arrived"] == 2
    current = rows[1]
    assert current["now"] and current["arrived"] == 0
    assert current["expected"] == forecast[0]["people"]
    assert current["expected"] >= 3
    future = rows[2:]
    assert all(r["arrived"] is None for r in future)
    assert [r["expected"] for r in future] == [r["people"] for r in forecast[1:]]
    assert sum(r["expected"] for r in rows[1:]) == pytest.approx(7, abs=0.05 * 4)


def test_timeline_empty_race_is_one_current_row():
    now = _at(15, 20)
    assert ff.build_timeline([], [1], now) == {
        key: [
            {"from": "15:00", "to": "16:00", "arrived": 0, "expected": 0.0, "now": True}
        ]
        for key in ("all", "1")
    }


@pytest.mark.parametrize("finish", [1_760_000_000, None])
def test_timeline_old_finishes_fold_into_earlier_row(finish):
    now = _at(15, 20)
    finish = finish or now - 48 * 60 * MIN
    teams = [_finished(1, 2, finish), _finished(1, 3, _at(15, 5))]
    rows = ff.build_timeline(teams, [1], now)["all"]
    assert rows[0] == {
        "from": None,
        "to": "15:00",
        "arrived": 2,
        "expected": None,
        "now": False,
    }
    assert len(rows) == 1 + 24 + 1
    assert rows[1]["from"] == "15:00"
    assert rows[-1]["now"] and rows[-1]["arrived"] == 3


def test_timeline_across_midnight():
    now = _at(1, 20, day=10)
    teams = [_finished(1, 2, _at(23, 30)), _team(1, 3, now - 600 * MIN, now)]
    rows = ff.build_timeline(teams, [1], now)["all"]
    assert rows[0]["from"] == "23:00"
    assert [r["now"] for r in rows].index(True) == 2
    assert rows[0]["arrived"] == 2


# --- race_finishes_data -------------------------------------------------------

FINISHES_URLS = ["race_finishes", "race_finishes_data", "race_finishes_print"]


def _data(client, race):
    resp = client.get(reverse("race_finishes_data", kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200
    return resp.json()


def _category(race, control_time=720, code="12h", order=0):
    category = _make_category(race, code=code, order=order)
    category.control_time = control_time
    category.save()
    return category


def _now_ms():
    return int(timezone.now().timestamp() * 1000)


@pytest.mark.django_db
@pytest.mark.parametrize("name", FINISHES_URLS)
def test_finishes_anonymous_redirects_to_login(client, name):
    race = _make_race()
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 302
    assert resp.url.startswith(reverse("login"))
    assert "next=" in resp.url


@pytest.mark.django_db
@pytest.mark.parametrize("name", FINISHES_URLS)
@pytest.mark.parametrize("who", ["plain", "moderator", "superuser"])
def test_finishes_forbidden(client, django_user_model, name, who):
    race = _make_race()
    if who == "plain":
        user = django_user_model.objects.create_user(username="u")
    elif who == "moderator":
        user = _admin(django_user_model, race, role=RaceAdmin.Role.MODERATOR)
    else:
        user = django_user_model.objects.create_superuser(username="r", password="x")
    client.force_login(user)
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("name", FINISHES_URLS)
def test_finishes_race_admin_allowed(client, django_user_model, name):
    race = _make_race()
    client.force_login(_admin(django_user_model, race))
    resp = client.get(reverse(name, kwargs={"race_slug": race.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_finishes_data_team_states(client, django_user_model):
    race = _make_race()
    timed = _category(race)
    untimed = _category(race, control_time=0, code="free", order=1)
    admin = _admin(django_user_model, race)
    now = _now_ms()
    hour = 60 * MIN
    specs = {
        "1": (timed, now - 5 * hour, now - hour, ff.FINISHED, False),
        "2": (timed, now - hour, 0, ff.ON_COURSE, False),
        "3": (timed, now - 12 * hour - 30 * MIN, 0, ff.OVERDUE, False),
        "4": (timed, now - 14 * hour, 0, ff.OVERDUE, True),
        "5": (timed, 0, 0, ff.NOT_STARTED, False),
        "6": (untimed, now - hour, 0, ff.NO_CONTROL, False),
    }
    for number, (category, start, finish, _, _) in specs.items():
        _make_team(
            admin, category, start_number=number, start_time=start, finish_time=finish
        )
    _make_team(admin, timed, start_number="7", paid_people=0, start_time=now - hour)
    _make_team(admin, timed, start_number="8", is_deleted=True, start_time=now - hour)
    other = _category(_make_race(slug="other"))
    _make_team(admin, other, start_number="9", start_time=now - hour)
    client.force_login(admin)
    data = _data(client, race)
    got = {t["start_number"]: (t["state"], t["overdue_long"]) for t in data["teams"]}
    assert got == {n: (s[3], s[4]) for n, s in specs.items()}
    by_number = {t["start_number"]: t for t in data["teams"]}
    assert by_number["6"]["deadline"] is None
    assert by_number["5"]["deadline_ms"] is None
    assert data["server_time_ms"] >= now


@pytest.mark.django_db
def test_finishes_data_deadline_in_project_time_zone(client, django_user_model):
    race = _make_race()
    admin = _admin(django_user_model, race)
    start = _at(9, 14, 5)
    _make_team(admin, _category(race), start_time=start)
    client.force_login(admin)
    (team,) = _data(client, race)["teams"]
    assert team["start_time"] == "09:14:05"
    assert team["deadline_ms"] == start + 720 * MIN
    assert team["deadline"] == "21:14"
    assert isinstance(team["people"], int)


@pytest.mark.django_db
def test_finishes_data_counts_team_size_not_paid_seats(client, django_user_model):
    race = _make_race()
    category = _category(race)
    admin = _admin(django_user_model, race)
    now = _now_ms()
    _make_team(admin, category, start_number="1", paid_people=4, ucount=3)
    _make_team(
        admin, category, start_number="2", paid_people=2, ucount=5, start_time=now
    )
    client.force_login(admin)
    data = _data(client, race)
    assert [t["people"] for t in data["teams"]] == [3, 5]
    expected = sum(r["expected"] or 0 for r in data["timeline"]["all"])
    assert expected == pytest.approx(5, abs=0.2)


@pytest.mark.django_db
@pytest.mark.parametrize("finish", [-5, 2**62])
def test_finishes_data_garbage_finish_is_null(client, django_user_model, finish):
    race = _make_race()
    admin = _admin(django_user_model, race)
    _make_team(admin, _category(race), start_time=_now_ms(), finish_time=finish)
    client.force_login(admin)
    (team,) = _data(client, race)["teams"]
    assert team["finish_time_ms"] is None
    assert team["finish_time"] is None
    assert team["state"] == ff.ON_COURSE


@pytest.mark.django_db
def test_finishes_data_timeline(client, django_user_model):
    race = _make_race()
    timed = _category(race)
    untimed = _category(race, control_time=0, code="free", order=1)
    admin = _admin(django_user_model, race)
    now = _now_ms()
    _make_team(admin, timed, start_number="1", ucount=4, start_time=now - 60 * MIN)
    _make_team(admin, timed, start_number="2", ucount=3, start_time=now - 730 * MIN)
    _make_team(
        admin,
        untimed,
        start_number="3",
        ucount=5,
        start_time=now - 120 * MIN,
        finish_time=now - MIN,
    )
    _make_team(
        admin,
        timed,
        start_number="4",
        ucount=2,
        start_time=now - 60 * MIN,
        finish_time=2**62,
    )
    client.force_login(admin)
    data = _data(client, race)
    assert "forecast" not in data
    timeline = data["timeline"]
    assert set(timeline) == {"all", str(timed.id), str(untimed.id)}
    assert [c["control_time"] for c in data["categories"]] == [720, 0]
    rows = timeline["all"]
    current = next(r for r in rows if r["now"])
    assert current["expected"] >= 3
    expected = [r["expected"] for r in rows if r["expected"] is not None]
    assert sum(expected) == pytest.approx(9, abs=0.05 * len(expected))
    assert sum(r["arrived"] for r in rows if r["arrived"] is not None) == 5
    untimed_rows = timeline[str(untimed.id)]
    assert sum(r["arrived"] or 0 for r in untimed_rows) == 5
    assert all(not r["expected"] for r in untimed_rows)
    assert sum(r["arrived"] or 0 for r in timeline[str(timed.id)]) == 0


@pytest.mark.django_db
def test_finishes_page_embeds_data_url(client, django_user_model):
    race = _make_race()
    client.force_login(_admin(django_user_model, race))
    resp = client.get(reverse("race_finishes", kwargs={"race_slug": race.slug}))
    html = resp.content.decode()
    assert 'id="rfForecast"' in html
    kwargs = {"race_slug": race.slug}
    print_url = reverse("race_finishes_print", kwargs=kwargs)
    assert _script_json(html, "raceFinishesConfig") == {
        "dataUrl": reverse("race_finishes_data", kwargs=kwargs),
        "printUrl": print_url,
    }
    assert f'id="rfPrint" href="{print_url}"' in html


@pytest.mark.django_db
def test_race_page_finishes_button_only_for_race_admin(client, django_user_model):
    race = _make_race()
    race.is_published = True
    race.save()
    url = reverse("race", kwargs={"race_slug": race.slug})
    finishes_url = reverse("race_finishes", kwargs={"race_slug": race.slug})

    client.force_login(django_user_model.objects.create_user(username="u"))
    assert finishes_url not in client.get(url).content.decode()

    client.force_login(_admin(django_user_model, race))
    assert finishes_url in client.get(url).content.decode()


# --- race_finishes_print ------------------------------------------------------


def _print(client, race, **params):
    url = reverse("race_finishes_print", kwargs={"race_slug": race.slug})
    resp = client.get(url, params)
    assert resp.status_code == 200
    return resp


def _print_race(django_user_model):
    race = _make_race()
    timed = _category(race)
    other = _category(race, code="6h", order=1)
    admin = _admin(django_user_model, race)
    now = _now_ms()
    hour = 60 * MIN
    _make_team(admin, timed, start_number="1", ucount=4, start_time=now - hour)
    _make_team(
        admin,
        timed,
        start_number="2",
        ucount=3,
        start_time=now - 5 * hour,
        finish_time=now - 2 * hour,
    )
    _make_team(admin, other, start_number="3", ucount=2, start_time=now - hour)
    _make_team(admin, other, start_number="4", ucount=5)
    _make_team(admin, other, start_number="5", ucount=6, start_time=now - 14 * hour)
    return race, timed, other, admin


@pytest.mark.parametrize("value, shown", [(2.5, 3), (2.4, 2), (0.0, 0), (3.45, 4)])
def test_half_up_matches_math_round(value, shown):
    from apps.race.views import _half_up

    assert _half_up(value) == shown


@pytest.mark.django_db
def test_finishes_print_sheet(client, django_user_model):
    race, timed, other, admin = _print_race(django_user_model)
    client.force_login(admin)
    resp = _print(client, race)
    html = resp.content.decode()
    assert "<script" not in html
    assert html.count("← сейчас") == 1
    assert "приход людей на финиш" in html
    assert "Всего" in html
    assert "не стартовали — 5 чел." in html
    assert "опаздывающие больше часа — 6 чел." in html
    assert resp.context["arrived_total"] == 3
    assert resp.context["expected_total"] == 6
    assert sum(1 for r in resp.context["rows"] if r["now"]) == 1


@pytest.mark.django_db
def test_finishes_print_category_matches_live_timeline(client, django_user_model):
    race, timed, other, admin = _print_race(django_user_model)
    client.force_login(admin)
    timeline = _data(client, race)["timeline"]
    for category, arrived, expected in ((timed, 3, 4), (other, 0, 2)):
        rows = timeline[str(category.id)]
        resp = _print(client, race, category=category.id)
        assert resp.context["selected_category"] == category
        assert resp.context["arrived_total"] == arrived
        assert resp.context["expected_total"] == expected
        assert sum(r["arrived"] or 0 for r in rows) == arrived
        assert round(sum(r["expected"] or 0 for r in rows)) == expected
        assert len(resp.context["rows"]) == len(rows)
    html = _print(client, race, category=other.id).content.decode()
    assert "не стартовали — 5 чел." in html
    assert "категория 6h" in html


@pytest.mark.django_db
def test_finishes_print_unknown_category_is_whole_race(client, django_user_model):
    race, timed, other, admin = _print_race(django_user_model)
    client.force_login(admin)
    resp = _print(client, race, category="junk")
    assert resp.context["selected_category"] is None
    assert resp.context["arrived_total"] == 3


@pytest.mark.django_db
def test_finishes_print_without_teams(client, django_user_model):
    race = _make_race()
    _category(race)
    client.force_login(_admin(django_user_model, race))
    rows = _print(client, race).context["rows"]
    assert len(rows) == 1
    assert rows[0]["now"] and rows[0]["arrived"] == 0 and rows[0]["expected"] == 0
