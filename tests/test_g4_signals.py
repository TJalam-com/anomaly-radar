"""Planted-case tests per signal (design notes r4 §3, r5 Δ1, r6 Δ3/Δ6, r7). Each asserts the flip the note specifies."""
import math

import pytest

from g4_fixture import RES, World, W, run, sig

C, C2, C3, C4, C5 = "c1", "c2", "c3", "c4", "c5"
H = 3600


def big_lowodds(w, world, cond=C, ts=RES - 10 * H, size=50_000, price=0.10, idx=0):
    return world.fill(cond, w, "BUY", size, price, ts, idx)


# ---------------------------------------------------------------- S3
def test_s3_floorlog():
    w = World().market(C)
    big_lowodds(W(1), w)                                  # 5,000 USDC @0.10
    w.fill(C, W(2), "BUY", 9_000, 0.10, RES - H)          # 900 USDC
    w.fill(C, W(3), "BUY", 200, 0.05, RES - H)            # 10 USDC lottery ticket
    w.fill(C, W(4), "BUY", 50_000, 0.25, RES - H)         # 12,500 USDC but price >= p_low
    w.fill(C, W(5), "BUY", 50_000, 0.10, RES)             # same second as resolution -> excluded
    con = run(w)
    assert sig(con, W(1), "S3")[1] == pytest.approx(math.log10(5) / 2, abs=1e-9)
    for x in (2, 3, 4, 5):
        assert sig(con, W(x), "S3")[1] == 0.0


# ---------------------------------------------------------------- S5
def test_s5_poisson_binomial():
    w = World()
    for c in (C, C2, C3, C4, C5):
        w.market(c, winner=0)
        w.fill(c, W(1), "BUY", 1000, 0.10, RES - H)       # 5 wins at 0.10
        w.fill(c, W(2), "BUY", 1000, 0.95, RES - H)       # 5 wins at 0.95
    w.market("cv", void=True)
    w.fill("cv", W(1), "BUY", 1000, 0.10, RES - H)        # void bet: excluded
    for c in (C, C2):
        w.fill(c, W(3), "BUY", 1000, 0.10, RES - H)       # only 2 bets
    con = run(w)
    r1 = sig(con, W(1), "S5")
    assert r1[0] == pytest.approx(5.0, abs=1e-6) and r1[1] == pytest.approx(5 / 6, abs=1e-6)
    assert sig(con, W(2), "S5")[1] == pytest.approx(-math.log10(0.95 ** 5) / 6, abs=1e-6)
    assert sig(con, W(3), "S5")[2] == "n_resolved<min"
    assert sig(con, W(1), "S5", scope="cv")[2] == "no_winner"


# ---------------------------------------------------------------- S7
def test_s7_coordination():
    w = World().market(C)
    for i in range(4):
        w.fill(C, W(10 + i), "BUY", 2000 / 0.12, 0.12, RES - 10 * H + i * 60)   # 4 wallets within 3 min
    for i in range(4):
        w.fill(C, W(20 + i), "BUY", 2000 / 0.12, 0.12, RES - 30 * H + i * 1200)  # 20 min apart
    con = run(w)
    assert sig(con, W(10), "S7")[0] == 3 and sig(con, W(10), "S7")[1] == pytest.approx(0.6)
    assert sig(con, W(20), "S7")[1] == 0.0
    w2 = World().market(C)
    for i in range(4):
        w2.fill(C, W(10 + i), "BUY", 2000 / 0.12, 0.30 if i == 3 else 0.12, RES - 10 * H + i * 60)
    con2 = run(w2)
    assert sig(con2, W(10), "S7")[0] == 2 and sig(con2, W(13), "S7")[1] == 0.0


# ---------------------------------------------------------------- S4
def test_s4_anchor_window():
    w = World().market(C, winner=0)
    a = RES - 48 * H
    w.anchors.append((C, a, 60, None))
    w.fill(C, W(1), "BUY", 20_000 / 0.3, 0.3, a - 3 * H)     # 20k USDC winning side, 3 h before
    w.fill(C, W(2), "BUY", 20_000 / 0.3, 0.3, a - 30 * H)    # 30 h before -> outside lead
    w.fill(C, W(3), "BUY", 20_000 / 0.3, 0.3, a + H)         # after the event
    con = run(w)
    assert sig(con, W(1), "S4")[1] == pytest.approx(math.log10(20) / math.log10(50), abs=1e-9)
    assert sig(con, W(1), "S4")[1] > 0.5
    assert sig(con, W(2), "S4")[1] == 0.0 and sig(con, W(3), "S4")[1] == 0.0


def test_s4_na_reasons():
    w = World().market(C).market(C2)
    w.anchors.append((C2, None, 86400, "event_ts_too_coarse"))
    w.fill(C, W(1), "BUY", 1000, 0.3, RES - H)
    w.fill(C2, W(2), "BUY", 1000, 0.3, RES - H)
    con = run(w)
    assert sig(con, W(1), "S4")[2] == "no_event_ts"
    assert sig(con, W(2), "S4")[2] == "event_ts_too_coarse"


# ---------------------------------------------------------------- S1
def test_s1_freshness_and_na():
    w = World().market(C)
    tb = RES - 10 * H
    for i, ftt in ((1, tb - 2 * H), (2, tb - 60 * 86400)):
        big_lowodds(W(i), w, ts=tb)
        w.profile[W(i)] = dict(ftt=ftt, vol=1e5)
    big_lowodds(W(3), w, ts=tb)
    w.profile[W(3)] = dict(ftt=None, act="unavailable")
    con = run(w)
    assert sig(con, W(1), "S1")[1] == 1.0
    assert sig(con, W(2), "S1")[1] == 0.0
    assert sig(con, W(3), "S1")[2] == "activity_unavailable"


# ---------------------------------------------------------------- S2
def test_s2_concentration_both_sides():
    w = World().market(C)
    w.fill(C, W(1), "BUY", 30_000, 0.10, RES - 10 * H)   # 3,000 low-odds (profiled)
    w.fill(C, W(1), "SELL", 13_000, 0.50, RES - 5 * H)   # 6,500 sell leg -> numerator 9,500 both sides
    w.profile[W(1)] = dict(ftt=RES - 100 * 86400, vol=10_000)
    con = run(w)
    assert sig(con, W(1), "S2")[1] == pytest.approx(0.95)
    w.profile[W(1)]["vol"] = 100_000
    assert sig(run(w), W(1), "S2")[1] == pytest.approx(0.095)


# ---------------------------------------------------------------- S6
def _s6_world():
    w = World().market(C)
    for i in (1, 2, 3, 4, 5, 6):
        big_lowodds(W(i), w)
        w.profile[W(i)] = dict(ftt=RES - 86400, vol=1e5)
    for i in (1, 2, 3):
        w.transfers.append((W(i), "in", 1, "0xfunder"))
    for i in (4, 5):
        w.transfers.append((W(i), "out", 1, "0xdeposit"))
    return w


def test_s6_in_and_out():
    w = _s6_world()
    con = run(w)
    assert sig(con, W(1), "S6")[0] == 2 and sig(con, W(1), "S6")[1] == pytest.approx(0.4)
    assert sig(con, W(4), "S6")[0] == 1 and sig(con, W(4), "S6")[1] == pytest.approx(0.2)
    assert sig(con, W(6), "S6")[2] == "no_transfers_found"
    con_in = run(w, s6_edges=("in",))                       # r6 Δ5 mode (iii)
    assert sig(con_in, W(4), "S6")[1] == 0.0 and sig(con_in, W(1), "S6")[1] == pytest.approx(0.4)


def test_s6_stop_list_and_fanout():
    w = _s6_world()
    w.stop += ["0xfunder", "0xdeposit"]
    con = run(w)
    assert sig(con, W(1), "S6")[1] == 0.0 and sig(con, W(4), "S6")[1] == 0.0
    w2 = _s6_world()
    con2 = run(w2, P={"s6_fanout_max": 1})                  # both addresses exceed fan-out 1
    assert sig(con2, W(1), "S6")[1] == 0.0 and sig(con2, W(4), "S6")[1] == 0.0


# ---------------------------------------------------------------- S8
def test_s8_exit_behaviour():
    w = World().market(C, winner=0)
    for i in (1, 2, 3, 4):
        big_lowodds(W(i), w)
        w.profile[W(i)] = dict(ftt=RES - 86400, vol=1e5, tsnap=RES + 90 * 86400)
        w.redeems.append((W(i), C, RES + 2 * H, "safe_exec"))
    w.trade_after += [(W(1), C, None), (W(2), C, RES + 2 * H + 5 * 86400), (W(3), C, None), (W(4), C, None)]
    w.profile[W(3)]["tsnap"] = RES + 10 * 86400
    w.redeems[3] = (W(4), C, RES + 2 * H, "other")
    con = run(w)
    assert sig(con, W(1), "S8")[1] == 1.0
    assert sig(con, W(2), "S8")[1] == 0.5
    assert sig(con, W(3), "S8")[2] == "dormancy_unobservable"
    assert sig(con, W(4), "S8")[2] == "auto_redeem_unknown"


# ---------------------------------------------------------------- prefilter, bypass, density, NA invariant
def test_prefilter_bypass_and_density():
    w = World().market(C).market(C2)
    w.fill(C, W(1), "BUY", 9_000, 0.10, RES - H)          # 900 USDC low-odds only -> not passed
    w.fill(C, W(2), "BUY", 6_000, 0.10, RES - H)          # 600 + 600 across two markets = 1,200 -> passed (Σ rule)
    w.fill(C2, W(2), "BUY", 6_000, 0.10, RES - H)
    w.fill(C, W(3), "BUY", 100, 0.5, RES - H)             # ordinary wallet
    con = run(w)
    u = dict(con.execute("SELECT proxy_wallet, profiled FROM universe_t WHERE scope='all'").fetchall())
    assert u == {W(1): False, W(2): True, W(3): False}
    assert sig(con, W(1), "S1")[2] == "not_profiled"
    n_u = con.execute("SELECT count(*) FROM universe_t").fetchone()[0]
    assert con.execute("SELECT count(*) FROM signals_dense").fetchone()[0] == 8 * n_u
    assert con.execute("SELECT count(*) FROM signals_dense WHERE (raw_value IS NULL) <> (na_reason IS NOT NULL)").fetchone()[0] == 0
    w.bypass.append(W(1))
    con2 = run(w)
    row = con2.execute("SELECT passed_prefilter, bypass_listed, profiled FROM universe_t WHERE scope='all' AND proxy_wallet=?", [W(1)]).fetchone()
    assert row == (False, True, True)
    assert sig(con2, W(1), "S1")[2] != "not_profiled"


# ---------------------------------------------------------------- QA funding-window rule (corrected) + S6 flag + frozen shapes
def test_s1_funding_window_truncation():
    lb_ts = RES - 400 * 86400          # time of the lower-bound block
    w = World().market(C)
    tb = RES - 10 * H
    big_lowodds(W(1), w, ts=tb)        # funding just after the bound, first trade after the bound -> NA
    w.profile[W(1)] = dict(ftt=lb_ts + 86400, fft=lb_ts + 3600, trunc=True, lb_ts=lb_ts, vol=1e5)
    big_lowodds(W(2), w, ts=tb)        # same, but a trade BEFORE the bound -> provably old -> computed, component 0
    w.profile[W(2)] = dict(ftt=lb_ts - 86400, fft=lb_ts + 3600, trunc=True, lb_ts=lb_ts, vol=1e5)
    big_lowodds(W(3), w, ts=tb)        # not truncated -> normal rule
    w.profile[W(3)] = dict(ftt=tb - 2 * H, fft=tb - 3 * H, trunc=False, lb_ts=lb_ts, vol=1e5)
    w.transfers += [(W(1), "in", 1, "0xa"), (W(2), "in", 1, "0xa")]
    con = run(w)
    assert sig(con, W(1), "S1")[2] == "funding_window_truncated"
    r2 = sig(con, W(2), "S1")
    assert r2[2] is None and r2[1] == 0.0
    assert sig(con, W(3), "S1")[1] == 1.0
    ev = con.execute("SELECT evidence_json FROM signals_dense WHERE scope='all' AND proxy_wallet=? AND signal_id='S6'", [W(1)]).fetchone()[0]
    assert '"edges_possibly_incomplete": true' in ev


def test_s8_relay_hub_not_user_signed_under_frozen_shapes():
    w = World().market(C, winner=0)
    big_lowodds(W(1), w)
    w.profile[W(1)] = dict(ftt=RES - 86400, vol=1e5, tsnap=RES + 90 * 86400)
    w.redeems.append((W(1), C, RES + 2 * H, "relay_hub"))
    w.trade_after.append((W(1), C, None))
    con = run(w, P={"_user_signed_shapes": ("safe_exec", "direct_eoa")})
    assert sig(con, W(1), "S8")[2] == "auto_redeem_unknown"
    w.redeems[0] = (W(1), C, RES + 2 * H, "direct_eoa")
    assert sig(run(w, P={"_user_signed_shapes": ("safe_exec", "direct_eoa")}), W(1), "S8")[1] == 1.0


# ---------------------------------------------------------------- QA determinism ruling 2026-09-27: exact sums
def test_exact_zero_net_position_is_not_a_bet():
    """BUY 0.1 + BUY 0.2 - SELL 0.3: float sum 5.55e-17 > 0, exact net 0 -> no S5 bet, no S8 winning bet."""
    w = World()
    for c in (C, C2, C3, C4):
        w.market(c, winner=0)
    for c in (C, C2, C3):
        w.fill(c, W(7), "BUY", 1000, 0.10, RES - H)
    for side, size, dt in (("BUY", 0.1, 3), ("BUY", 0.2, 2), ("SELL", 0.3, 1)):
        w.fill(C4, W(7), side, size, 0.10, RES - dt * H)
        w.fill(C4, W(8), side, size, 0.10, RES - dt * H)
    w.profile[W(8)] = dict(ftt=RES - 86400, vol=1e5, tsnap=RES + 90 * 86400)
    w.bypass.append(W(8))
    con = run(w)
    n = con.execute("SELECT count(*) FROM s5_bets WHERE scope='all' AND proxy_wallet=?", [W(7)]).fetchone()[0]
    assert n == 3                                                     # float would count the C4 residual as a 4th bet
    assert sig(con, W(7), "S5")[0] == pytest.approx(-math.log10(0.1 ** 3), abs=1e-9)
    assert sig(con, W(8), "S8")[2] == "no_winning_bet"               # float: residual > 0 on the winner -> S8 = 0.0


def test_prefilter_exact_sum_at_threshold():
    """160 @0.19 + 6464 @0.15 = exactly 1,000 USDC; the float sum is 999.9999999999999 -> must still pass (>=)."""
    w = World().market(C)
    w.fill(C, W(9), "BUY", 160, 0.19, RES - 2 * H)
    w.fill(C, W(9), "BUY", 6464, 0.15, RES - H)
    w.fill(C, W(10), "BUY", 16.1, 0.10, RES - H)                      # exact 1.61; DuckDB's direct cast gives 1.6099999999999999
    con = run(w)
    assert con.execute("SELECT passed_prefilter FROM universe_t WHERE scope='all' AND proxy_wallet=?", [W(9)]).fetchone()[0] is True
    assert sig(con, W(9), "S3")[0] == 1000.0                          # correctly rounded output of the exact sum
    assert sig(con, W(10), "S3")[0] == 1.61


def test_prefilter_threshold_compared_exactly():
    """Exact sum 999.9999999999999999 (5000.000002 @0.1999999999 + 1000.000001 @1e-10) rounds to 1000.0 as DOUBLE;
    compared exactly it is below pre_min_stake -> not passed."""
    w = World().market(C)
    w.fill(C, W(11), "BUY", 5000.000002, 0.1999999999, RES - 2 * H)
    w.fill(C, W(11), "BUY", 1000.000001, 1e-10, RES - H)
    con = run(w)
    assert con.execute("SELECT passed_prefilter FROM universe_t WHERE scope='all' AND proxy_wallet=?", [W(11)]).fetchone()[0] is False


def test_no_signed_zero_in_outputs():
    """S5 with every bet lost: pval = 1, -log10(1.0) = -0.0 in Python. Stored as +0.0: the parquet dictionary writer
    treats +-0 as one value (first sign wins per row group), so a signed zero makes file bytes thread-dependent."""
    w = World()
    for c in (C, C2, C3):
        w.market(c, winner=1)
        w.fill(c, W(12), "BUY", 1000, 0.10, RES - H)           # buys outcome 0; outcome 1 wins -> k = 0
    con = run(w)
    raw = sig(con, W(12), "S5")[0]
    assert raw == 0.0 and math.copysign(1.0, raw) == 1.0
    assert con.execute("SELECT count(*) FROM signals_dense WHERE CAST(raw_value AS VARCHAR) = '-0.0' OR CAST(component AS VARCHAR) = '-0.0'").fetchone()[0] == 0


def test_sparse_canonicalises_signed_zero_in_raw_and_component():
    """Tester R-5: _sparse maps -0.0 to +0.0 in BOTH raw_value and component (component paths cannot produce -0.0
    today, clip01/max keep the first argument; the guard is tested directly)."""
    from radar import signals as sg
    con = World().market(C).build()
    con.execute("""CREATE TEMP TABLE sig_sparse(scope TEXT, proxy_wallet TEXT, signal_id TEXT,
        raw_value DOUBLE, component DOUBLE, na_reason TEXT, evidence_json TEXT)""")
    sg._sparse(con, "S5", [("all", W(1), -0.0, -0.0, None, {})])
    raw, comp = con.execute("SELECT raw_value, component FROM sig_sparse").fetchone()
    assert math.copysign(1.0, raw) == 1.0 and math.copysign(1.0, comp) == 1.0
