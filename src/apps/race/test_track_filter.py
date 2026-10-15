"""Tests for the trackLines port; vectors from the Android ``TrackLinesTest.kt``."""

import math

import pytest

from apps.race.track_filter import (
    TrackFix,
    haversine_m,
    is_reachable,
    is_short_chain,
    point_time_ms,
    sort_key,
    track_lines,
)

BASE_LAT = 55.0
BASE_LON = 37.0
M_PER_DEG = 6_371_000.0 * math.pi / 180.0


def pt(id, t_sec, north_m, acc=10.0, seg="seg"):
    return TrackFix(
        id=id,
        install_id="i",
        segment_id=seg,
        lat=BASE_LAT + north_m / M_PER_DEG,
        lon=BASE_LON,
        accuracy=acc,
        altitude=None,
        time_ms=t_sec * 1000,
        boot_count=None,
        elapsed_at=0,
    )


def pt_east(id, t_sec, east_m, acc=10.0):
    lon = BASE_LON + east_m / (M_PER_DEG * math.cos(math.radians(BASE_LAT)))
    return pt(id, t_sec, 0.0, acc)._replace(lon=lon)


def walk(prefix, n, start_sec, start_m, acc=10.0):
    return [
        pt(f"{prefix}{i}", start_sec + 15 * i, start_m + 20.0 * i, acc)
        for i in range(n)
    ]


def ids(lines):
    return [[p.id for p in line] for line in lines]


def flat_ids(*chains):
    return [p.id for chain in chains for p in chain]


# ---- helpers ----


def test_point_time_prefers_trusted_and_treats_zero_as_absent():
    assert point_time_ms(5_000, 7_000) == 5_000
    assert point_time_ms(None, 7_000) == 7_000
    assert point_time_ms(0, 7_000) == 7_000


def test_sort_key_orders_by_time_then_boot_then_elapsed_then_id():
    a = pt("b", 10, 0)._replace(boot_count=None, elapsed_at=5)
    b = pt("a", 10, 0)._replace(boot_count=0, elapsed_at=1)
    c = pt("c", 10, 0)._replace(boot_count=0, elapsed_at=2)
    d = pt("d", 5, 0)._replace(boot_count=9)
    assert [p.id for p in sorted([c, b, a, d], key=sort_key)] == ["d", "b", "a", "c"]


def test_haversine():
    assert haversine_m(55.0, 37.0, 56.0, 37.0) == pytest.approx(M_PER_DEG, abs=0.01)
    assert haversine_m(0.0, 10.0, 0.0, 11.0) == pytest.approx(M_PER_DEG, abs=0.01)
    assert haversine_m(55.0, 37.0, 55.0, 38.0) == pytest.approx(
        M_PER_DEG * math.cos(math.radians(55.0)), abs=1.0
    )
    assert haversine_m(55.7558, 37.6173, 59.9343, 30.3351) == pytest.approx(
        634_000.0, abs=3_000.0
    )
    assert haversine_m(55.75, 37.62, 55.75, 37.62) == 0.0


def test_is_reachable_speed_boundary():
    a = pt("a", 0, 0.0, acc=0.0)
    assert is_reachable(a, pt("b", 10, 139.9, acc=0.0))
    assert not is_reachable(a, pt("b", 10, 140.1, acc=0.0))


def test_is_reachable_subtracts_better_accuracy():
    assert is_reachable(pt("a", 0, 0.0, acc=60.0), pt("b", 15, 250.0, acc=90.0))
    assert not is_reachable(pt("a", 0, 0.0, acc=10.0), pt("b", 15, 250.0, acc=90.0))


def test_short_chain_boundaries():
    assert is_short_chain([pt("a", 0, 0.0)])
    assert is_short_chain([pt("a", 0, 0.0), pt("b", 30, 0.0), pt("c", 59, 0.0)])
    assert not is_short_chain([pt("a", 0, 0.0), pt("b", 30, 0.0), pt("c", 60, 0.0)])
    assert not is_short_chain(walk("w", 4, 0, 0.0))


# ---- track_lines ----


def test_spike_between_short_chains_dropped_into_one_line():
    points = [
        pt("A", 0, 0.0),
        pt("B", 15, 20.0),
        pt("X", 30, 1000.0),
        pt("C", 45, 60.0),
        pt("D", 60, 80.0),
    ]
    assert ids(track_lines(points)) == [["A", "B", "C", "D"]]


def test_short_tail_after_short_chain_kept_as_own_line():
    points = [
        pt("A", 0, 0.0, acc=5.0),
        pt("B", 15, 200.0, acc=20.0),
        pt("C", 30, 500.0),
    ]
    assert ids(track_lines(points)) == [["A", "B"], ["C"]]


@pytest.mark.parametrize("offset,acc", [(600.0, 450.0), (300.0, 8.0)])
def test_spike_between_long_chains_dropped(offset, acc):
    first = walk("a", 5, 0, 0.0, acc=15.0)
    spike = pt("S", 75, 80.0 + offset, acc=acc)
    second = walk("b", 5, 90, 120.0, acc=15.0)
    assert ids(track_lines(first + [spike] + second)) == [flat_ids(first, second)]


def test_slow_noisy_jitter_kept_as_one_line():
    offsets = [0.0, 80.0, -60.0, 90.0, -80.0, 70.0, 0.0, -90.0]
    accs = [60.0, 100.0, 70.0, 90.0, 80.0, 100.0, 60.0, 75.0]
    points = [pt(f"j{i}", 15 * i, offsets[i] + 5.0 * i, accs[i]) for i in range(8)]
    assert ids(track_lines(points)) == [flat_ids(points)]


def test_jitter_needing_noise_allowance_kept_as_one_line():
    points = [
        pt(f"j{i}", 15 * i, 0.0 if i % 2 == 0 else 250.0, acc=60.0 + i)
        for i in range(6)
    ]
    assert ids(track_lines(points)) == [flat_ids(points)]


def test_east_west_uses_longitude_cosine():
    points = [pt_east("a", 0, 0.0), pt_east("b", 15, 200.0)]
    assert ids(track_lines(points)) == [["a", "b"]]
    first = [pt_east(f"a{i}", 15 * i, 20.0 * i) for i in range(5)]
    spike = pt_east("S", 75, 880.0)
    second = [pt_east(f"b{i}", 90 + 15 * i, 120.0 + 20.0 * i) for i in range(5)]
    assert ids(track_lines(first + [spike] + second)) == [flat_ids(first, second)]


def test_two_mutually_unreachable_spikes_both_kept():
    left = walk("l", 5, 0, 0.0)
    x = pt("X", 75, 3000.0)
    y = pt("Y", 90, -3000.0)
    right = walk("r", 5, 105, 120.0)
    assert ids(track_lines(left + [x, y] + right)) == [
        flat_ids(left),
        ["X"],
        ["Y"],
        flat_ids(right),
    ]


def test_live_tail_kept_until_next_fix_makes_it_bypassable():
    gps = walk("g", 5, 0, 0.0)
    jump = pt("X", 75, 2000.0)
    assert ids(track_lines(gps + [jump])) == [flat_ids(gps), ["X"]]
    nxt = pt("g5", 90, 100.0)
    assert ids(track_lines(gps + [jump, nxt])) == [flat_ids(gps, [nxt])]


def test_head_network_cluster_before_long_chain_dropped():
    cluster = [
        pt("n0", 0, 3000.0, acc=300.0),
        pt("n1", 15, 3050.0, acc=320.0),
        pt("n2", 30, 2980.0, acc=280.0),
    ]
    gps = walk("g", 6, 45, 0.0)
    assert ids(track_lines(cluster + gps)) == [flat_ids(gps)]


def test_short_head_before_short_chain_kept():
    head = [pt("h0", 0, 0.0), pt("h1", 15, 20.0)]
    nxt = [pt("s0", 30, 2000.0), pt("s1", 45, 2020.0)]
    assert ids(track_lines(head + nxt)) == [["h0", "h1"], ["s0", "s1"]]


def test_trailing_network_fixes_after_long_chain_dropped():
    gps = walk("g", 5, 0, 0.0)
    tail = [pt("n0", 75, 2000.0, acc=300.0), pt("n1", 90, 2050.0, acc=310.0)]
    assert ids(track_lines(gps + tail)) == [flat_ids(gps)]


def test_trailing_gps_quality_chain_kept_as_own_line():
    gps = walk("g", 5, 0, 0.0)
    tail = [pt("t0", 75, 1080.0, acc=12.0), pt("t1", 90, 1100.0, acc=12.0)]
    assert ids(track_lines(gps + tail)) == [flat_ids(gps), ["t0", "t1"]]


def long_with_accs(accs=(5.0, 50.0, 10.0, 50.0, 5.0)):
    return [pt(f"g{i}", 15 * i, 20.0 * i, acc=acc) for i, acc in enumerate(accs)]


def far_tail(*accs):
    return [
        pt(f"t{i}", 75 + 15 * i, 2000.0 + 20.0 * i, acc=acc)
        for i, acc in enumerate(accs)
    ]


@pytest.mark.parametrize(
    "tail_accs,dropped",
    [
        ((30.0, 30.0), True),
        ((29.0, 29.0), False),
        ((29.0, 31.0), True),
        ((20.0, 38.0), False),
    ],
)
def test_tail_ratio_against_long_chain_median(tail_accs, dropped):
    gps = long_with_accs()
    tail = far_tail(*tail_accs)
    expected = [flat_ids(gps)] if dropped else [flat_ids(gps), flat_ids(tail)]
    assert ids(track_lines(gps + tail)) == expected


def test_tail_after_short_chain_kept_even_when_much_worse():
    left = walk("l", 5, 0, 0.0)
    short = [pt("s0", 75, 2000.0), pt("s1", 90, 2020.0)]
    tail = [pt("t0", 105, 6000.0, acc=300.0), pt("t1", 120, 6020.0, acc=300.0)]
    assert ids(track_lines(left + short + tail)) == [
        flat_ids(left),
        flat_ids(short),
        flat_ids(tail),
    ]


def test_tail_reference_median_floored_at_one_meter():
    gps = long_with_accs([0.0] * 5)
    kept = far_tail(2.0, 2.0)
    assert ids(track_lines(gps + kept)) == [flat_ids(gps), flat_ids(kept)]
    assert ids(track_lines(gps + far_tail(3.0, 3.0))) == [flat_ids(gps)]


def test_interior_short_chain_with_unreachable_bypass_kept():
    first = walk("a", 5, 0, 0.0)
    mid = [pt("m0", 75, 1000.0), pt("m1", 90, 1020.0)]
    second = walk("b", 5, 105, 5000.0)
    assert ids(track_lines(first + mid + second)) == [
        flat_ids(first),
        ["m0", "m1"],
        flat_ids(second),
    ]


def test_two_long_chains_with_unreachable_step_two_lines():
    first = walk("a", 5, 0, 0.0)
    second = walk("b", 5, 75, 3000.0)
    assert ids(track_lines(first + second)) == [flat_ids(first), flat_ids(second)]


def test_run_of_only_short_chains_keeps_every_point():
    points = [pt(f"p{i}", 15 * i, 2000.0 * i) for i in range(4)]
    lines = track_lines(points)
    assert len(lines) == 4
    assert [p.id for line in lines for p in line] == flat_ids(points)


def test_different_segment_ids_never_merged():
    points = [
        pt("a0", 0, 0.0, seg="a"),
        pt("a1", 15, 20.0, seg="a"),
        pt("b0", 30, 40.0, seg="b"),
        pt("b1", 45, 60.0, seg="b"),
    ]
    assert ids(track_lines(points)) == [["a0", "a1"], ["b0", "b1"]]


def test_each_segment_run_filtered_on_its_own():
    a = [p._replace(segment_id="a") for p in walk("a", 3, 0, 0.0)]
    b_head = [
        pt("bn0", 60, 5000.0, acc=300.0, seg="b"),
        pt("bn1", 75, 5050.0, acc=300.0, seg="b"),
    ]
    b_gps = [p._replace(segment_id="b") for p in walk("b", 5, 90, 100.0)]
    a2 = [p._replace(segment_id="a") for p in walk("c", 2, 180, 200.0)]
    assert ids(track_lines(a + b_head + b_gps + a2)) == [
        flat_ids(a),
        flat_ids(b_gps),
        flat_ids(a2),
    ]


def test_hard_cap():
    points = [
        pt("under", 0, 0.0, acc=499.9),
        pt("at", 15, 10.0, acc=500.0),
        pt("fine", 30, 20.0),
    ]
    assert ids(track_lines(points)) == [["under", "fine"]]
    assert track_lines([pt("a", 0, 0.0, acc=600.0), pt("b", 15, 20.0, acc=900.0)]) == []


def test_cell_tower_cluster_at_exactly_500_dropped():
    # Sparse fixes make a 4 km jump "reachable" and the long 500 m cluster is never
    # short, so only the strict cap removes it.
    left = walk("l", 5, 0, 0.0, acc=5.0)
    tower = [pt(f"c{i}", 900 + 30 * i, 4000.0, acc=500.0) for i in range(6)]
    right = walk("r", 5, 1800, 200.0, acc=5.0)
    assert ids(track_lines(left + tower + right)) == [flat_ids(left, right)]


def test_hard_cap_removes_spikes_that_would_split_the_track():
    left = walk("l", 5, 0, 0.0)
    x = pt("X", 75, 3000.0, acc=501.0)
    y = pt("Y", 90, -3000.0, acc=600.0)
    right = walk("r", 5, 105, 120.0)
    assert ids(track_lines(left + [x, y] + right)) == [flat_ids(left, right)]


def test_zero_dt_uses_one_second_floor():
    near = [pt("a", 0, 0.0, acc=5.0), pt("b", 0, 10.0, acc=5.0)]
    assert ids(track_lines(near)) == [["a", "b"]]
    far = [pt("a", 0, 0.0, acc=5.0), pt("b", 0, 30.0, acc=5.0)]
    assert ids(track_lines(far)) == [["a"], ["b"]]


def test_empty_and_single_point():
    assert track_lines([]) == []
    assert ids(track_lines([pt("a", 0, 0.0)])) == [["a"]]
