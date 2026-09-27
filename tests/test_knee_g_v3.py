"""params v3 s6_g_formula / s6_g_population / s6_capped_at_t (radar/knee_g.py; D1 r16b §3, r16c §1, r16d §2)."""
import pytest

from radar.knee_g import GUndefined, activity_at, g_from_activities, g_from_histogram, g_population, histogram

SNAP = 1_000


def rec(n_y, nat, complete=True, capped=False, cap_block=None, fb=10):
    return {"first_blocks": [fb] * n_y, "n_natural_sharers": nat, "complete": complete, "capped": capped,
            "cap_block": cap_block if cap_block is not None else (SNAP - 1 if capped else None)}


def test_prof001_proxy_histogram_gives_63():
    assert g_from_histogram([268, 425, 267, 235, 158, 127, 52, 50, 301]) == (63, 1)


def test_mode_tie_takes_lowest_bin():
    assert g_from_histogram([10, 100, 100, 40, 0, 0, 0, 0, 0]) == (16, 1)


def test_no_drop_gives_clamp_max():
    assert g_from_histogram([1, 2, 4, 8, 16, 32, 64, 128, 256]) == (256, 7)


def test_drop_into_terminal_bin_gives_255():
    assert g_from_histogram([0, 0, 0, 0, 0, 10, 10, 10, 4]) == (255, 5)


def test_exact_half_is_not_a_drop():
    assert g_from_histogram([0, 0, 0, 0, 100, 50, 10, 0, 0]) == (63, 4)


def test_terminal_bin_and_low_bins():
    assert histogram([None, 256, 300, 255, 1, 0, 2, 3, 4, 600]) == [2, 2, 1, 0, 0, 0, 0, 1, 4]


def test_low_clamp_and_undefined():
    assert g_from_activities([2] * 10 + [4])["G"] == 16
    with pytest.raises(GUndefined):
        g_from_histogram([0] * 8 + [5])


def test_population_natural_only_complete_only():
    base = [rec(a, 2) for a in [3] * 100 + [5] * 80 + [9] * 70 + [20] * 60 + [40] * 50 + [80] * 10]
    g0 = g_from_activities(g_population(base, SNAP))["G"]
    assert g0 == 63
    assert g_from_activities(g_population(base + [rec(200, 1)] * 500 + [rec(100, 0)] * 500, SNAP))["G"] == g0
    assert g_from_activities(g_population(base + [rec(9, 2, complete=False)] * 999, SNAP))["G"] == g0


def test_capped_at_t_only():
    assert g_population([rec(10, 2, capped=True, cap_block=SNAP + 50)], SNAP) == [10]
    assert g_population([rec(10, 2, capped=True, cap_block=SNAP)], SNAP) == [None]


def test_first_block_equal_to_block_t_counts_and_post_cut_excluded():
    assert activity_at({"capped": False, "cap_block": None, "first_blocks": [100, 400, 401]}, 400) == 2
    assert activity_at({"capped": True, "cap_block": 500, "first_blocks": [10, 20, 30, 600]}, 400) == 3
