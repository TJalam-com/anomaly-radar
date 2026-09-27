"""S6 v3 (params v3; D1 notes r16, r16a, r16b §5, r16c §2, r16d §1). New file: test_g4_signals.py (v2 S6) is untouched."""
import json
import math

import pytest

from g4_fixture import RES, World, W, run, sig

C = "c1"
H = 3600
T_PRE = RES - 5 * H
BT = 5_000                     # block(t) for the fixture as-of instant
G = 63
P3 = dict(s6_rule="C1", s6_max_hops=1, s6_graded_k=20, s6_fanout_max=0, _s6_G=G, _block_t=BT)
X1, X2, X3 = ("0x" + c * 40 for c in "abc")


def graded(n):
    return min(1.0, math.log1p(n) / math.log1p(20))


def world_with(naturals=(), bypass=()):
    w = World().market(C)
    for a in naturals:
        w.fill(C, a, "BUY", 50_000, 0.10, RES - 10 * H)          # passes the prefilter -> natural
        w.profile[a] = dict(ftt=RES - 86400, fft=RES - 90000, vol=1e5, tsnap=RES + 90 * 86400)
    for b in bypass:                                           # injected: profiled via the bypass list, not natural
        w.fill(C, b, "BUY", 100, 0.50, RES - 10 * H)            # in U (has a fill) but below the prefilter
        w.bypass.append(b)
        w.profile[b] = dict(ftt=RES - 86400, fft=RES - 90000, vol=1e5, tsnap=RES + 90 * 86400)
    return w


def edge(w, x, d="in", fb=100):
    return (w, d, 1, x, None, False, False, T_PRE, fb)


def lk(n_y, fb=10, **kw):
    return dict(first_blocks=[fb] * n_y, **kw)


def ev(con, w):
    return json.loads(con.execute("SELECT evidence_json FROM signals_dense WHERE scope='all' AND proxy_wallet=? AND signal_id='S6'", [w]).fetchone()[0])


def counts(con):
    return dict(zip([d[0] for d in con.execute("SELECT * FROM s6v3_counts WHERE scope='all'").description],
                    con.execute("SELECT * FROM s6v3_counts WHERE scope='all'").fetchone()))


# ---- partners are natural only (R16A-5)
def test_natural_pair_links():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1, "out")]; w.lookups[X1] = lk(5)
    con = run(w, P3)
    assert sig(con, A, "S6")[:2] == (1.0, pytest.approx(graded(1))) and sig(con, B, "S6")[0] == 1.0


def test_co_injected_bypass_pair_never_links():
    B1, B2 = W(11), W(12)
    w = world_with([], [B1, B2]); w.transfers += [edge(B1, X1), edge(B2, X1)]     # no lookup: X1 is not in the lookup set
    con = run(w, P3)
    assert sig(con, B1, "S6")[:3] == (0.0, 0.0, None) and sig(con, B2, "S6")[:3] == (0.0, 0.0, None)
    assert counts(con)["n_lookup_set"] == 0 and counts(con)["n_activity_unverified"] == 0


def test_bypass_wallet_links_only_to_natural_partners():
    A, B1, B2 = W(1), W(11), W(12)
    w = world_with([A], [B1, B2]); w.transfers += [edge(A, X1), edge(B1, X1), edge(B2, X1)]; w.lookups[X1] = lk(5)
    con = run(w, P3)
    assert sig(con, B1, "S6")[0] == 1.0 and sig(con, B2, "S6")[0] == 1.0      # each: the natural A only, never each other
    assert sig(con, A, "S6")[0] == 0.0                                        # A's partners must be natural: none


# ---- activity threshold and unknown lookups
def test_activity_at_g_links_g_plus_1_does_not():
    A, B, C_, D = W(1), W(2), W(3), W(4)
    w = world_with([A, B, C_, D]); w.transfers += [edge(A, X1), edge(B, X1), edge(C_, X2), edge(D, X2)]
    w.lookups[X1] = lk(G); w.lookups[X2] = lk(G + 1)
    con = run(w, P3)
    assert sig(con, A, "S6")[0] == 1.0 and sig(con, C_, "S6")[0] == 0.0


def test_incomplete_lookup_never_links_and_gives_activity_unverified():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]
    w.lookups[X1] = lk(5, subranges=[{"from": 0, "to": 4000, "status": "ok"}, {"from": 4001, "to": 10_000, "status": "unavailable"}])
    con = run(w, P3)
    assert sig(con, A, "S6")[2] == "activity_unverified" and counts(con)["n_activity_unverified"] == 2


def test_missing_lookup_gives_activity_unverified():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]
    assert sig(run(w, P3), A, "S6")[2] == "activity_unverified"


def test_activity_unverified_not_applied_at_verified_component_1():
    A, others = W(1), [W(100 + i) for i in range(20)]
    w = world_with([A] + others)
    w.transfers += [edge(A, X1)] + [edge(o, X1) for o in others] + [edge(A, X2), edge(W(100), X2)]
    w.lookups[X1] = lk(30)                                    # X2 has no lookup (incomplete) -> unverified, but verified = 20
    con = run(w, P3)
    assert sig(con, A, "S6")[:3] == (20.0, 1.0, None) and ev(con, A).get("incomplete_path") is True
    assert counts(con)["n_s6_positive_on_incomplete_path"] >= 1


# ---- own hop-1 record incomplete (R16B-2 (i)) and precedence
def test_own_record_incomplete_below_1_is_na():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]; w.lookups[X1] = lk(5)
    w.expansions = [dict(proxy_wallet=A, direction="in", gap_free=False), dict(proxy_wallet=A, direction="out"),
                    dict(proxy_wallet=B, direction="in"), dict(proxy_wallet=B, direction="out")]
    con = run(w, P3)
    assert sig(con, A, "S6")[2] == "transfers_unverified" and sig(con, B, "S6")[0] == 1.0


def test_own_record_incomplete_at_1_keeps_value_and_counts():
    A, others = W(1), [W(100 + i) for i in range(20)]
    w = world_with([A] + others); w.transfers += [edge(A, X1)] + [edge(o, X1) for o in others]; w.lookups[X1] = lk(30)
    w.expansions = [dict(proxy_wallet=A, direction="in", gap_free=False), dict(proxy_wallet=A, direction="out")] + [
        dict(proxy_wallet=o, direction=d) for o in others for d in ("in", "out")]
    con = run(w, P3)
    assert sig(con, A, "S6")[:3] == (20.0, 1.0, None) and ev(con, A)["incomplete_path"] is True
    assert counts(con)["n_s6_positive_on_incomplete_path"] == 1


def test_precedence_transfers_unverified_over_activity_unverified():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]                       # X1 lookup missing
    w.expansions = [dict(proxy_wallet=A, direction="in", gap_free=False), dict(proxy_wallet=A, direction="out")]
    assert sig(run(w, P3), A, "S6")[2] == "transfers_unverified"


# ---- time: block(t) cut on edges and on activity (R16A-7, R16C-1)
def test_post_cut_edge_excluded_in_replay_cell():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1, fb=100), edge(B, X1, fb=4_000)]; w.lookups[X1] = lk(5)
    assert sig(run(w, P3), A, "S6")[0] == 1.0                                   # live: block(t) = 5000
    assert sig(run(w, dict(P3, _block_t=3_000)), A, "S6")[0] == 0.0             # replay cell before B's first transfer


def test_edge_in_block_t_counts_block_t_plus_1_does_not():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1, fb=BT), edge(B, X1, fb=BT)]; w.lookups[X1] = lk(5)
    assert sig(run(w, P3), A, "S6")[0] == 1.0
    w2 = world_with([A, B]); w2.transfers += [edge(A, X1, fb=BT + 1), edge(B, X1, fb=BT)]; w2.lookups[X1] = lk(5)
    con = run(w2, P3)
    assert sig(con, B, "S6")[:3] == (0.0, 0.0, None)                    # B's only possible partner edge is at block(t) + 1: no link
    assert sig(con, A, "S6")[2] == "no_transfers_found"                  # A has no hop-1 row at or before block(t) (R15V3-5)


def test_activity_growth_after_cut_does_not_disqualify_in_the_cell():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]
    w.lookups[X1] = dict(first_blocks=[10] * 60 + [4_500] * 40)                  # 60 before 4000, 100 by 5000
    assert sig(run(w, P3), A, "S6")[0] == 0.0                                   # live: activity 100 > G
    assert sig(run(w, dict(P3, _block_t=4_000)), A, "S6")[0] == 1.0             # cell: activity 60 <= G


def test_capped_after_block_t_uses_exact_count_capped_before_does_not_link():
    A, B = W(1), W(2)
    sub = [{"from": 0, "to": 7_000, "status": "ok", "capped_here": True}]
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]
    w.lookups[X1] = dict(first_blocks=[10] * 10 + [6_000] * 247, capped=True, cap_block=6_000, to_block=10_000, subranges=sub)
    assert sig(run(w, P3), A, "S6")[0] == 1.0                                   # cap after block(t): exact activity 10
    w.lookups[X1] = dict(first_blocks=[10] * 257, capped=True, cap_block=4_000, to_block=10_000, subranges=[dict(sub[0], to=4_000)])
    assert sig(run(w, P3), A, "S6")[0] == 0.0                                   # capped at t: > 256 > G


# ---- component and G plumbing
def test_graded_component():
    A, others = W(1), [W(100 + i) for i in range(4)]
    w = world_with([A] + others); w.transfers += [edge(A, X1)] + [edge(o, X1) for o in others]; w.lookups[X1] = lk(10)
    assert sig(run(w, P3), A, "S6")[1] == pytest.approx(math.log(5) / math.log(21))


def test_s6_v3_refuses_without_recorded_g():
    A = W(1)
    w = world_with([A])
    with pytest.raises(ValueError, match="_s6_G"):
        run(w, {k: v for k, v in P3.items() if k != "_s6_G"})


def test_activity_at_refuses_precomputed_value():
    from radar import knee_g
    with pytest.raises(ValueError, match="first_blocks"):
        knee_g.activity_at({"capped": False, "cap_block": None, "activity": 10}, 100)


# ---- Planner V1-1: --s6-edges in (sensitivity mode): same lookup set, records and G; inbound link edges only
def test_in_mode_outbound_only_share_links_by_default_not_in_in_mode():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1, "out"), edge(B, X1, "out")]; w.lookups[X1] = lk(5)
    assert sig(run(w, P3), A, "S6")[0] == 1.0
    con = run(w, P3, s6_edges=("in",))
    assert sig(con, A, "S6")[2] == "no_transfers_found" and counts(con)["n_lookup_set"] == 1   # no inbound edge (V1-1); same lookup set


def test_in_mode_inbound_share_still_links():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1, "in"), edge(B, X1, "in")]; w.lookups[X1] = lk(5)
    assert sig(run(w, P3, s6_edges=("in",)), A, "S6")[0] == 1.0


# ---- Tester 1 review R15V3-1/-2/-5
def test_replay_scope_is_refused_until_a_per_cell_path_exists():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1), edge(B, X1)]; w.lookups[X1] = lk(5)
    w.scopes["replay:c1:h24"] = [C]
    with pytest.raises(ValueError, match="per-cell"):
        run(w, P3)


def test_t_asof_is_required():
    from g4_fixture import P_DEFAULT
    from radar import signals
    con = world_with([W(1)]).build()
    P = dict(P_DEFAULT, **P3)
    signals.universe(con, P); signals.path_flags(con, P)          # everything before S6 with a valid t ...
    with pytest.raises(ValueError, match="S6 v3 needs"):          # ... then S6 v3 itself must refuse a missing t
        signals.s6_v3(con, dict(P, _t_asof_unix=None))


def test_no_transfers_found_is_as_of_block_t():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1, fb=BT + 100)]; w.lookups[X1] = lk(5)
    assert sig(run(w, P3), A, "S6")[2] == "no_transfers_found"                  # its only hop-1 row is after block(t)
    assert sig(run(w, dict(P3, _block_t=BT + 200)), A, "S6")[:3] == (0.0, 0.0, None)


def test_path_flags_requires_t():
    from g4_fixture import P_DEFAULT
    from radar import signals
    con = world_with([W(1)]).build()
    signals.universe(con, dict(P_DEFAULT))
    with pytest.raises(ValueError, match="path_flags needs"):
        signals.path_flags(con, dict(P_DEFAULT, _t_asof_unix=None))


def test_in_mode_na_uses_inbound_edges_and_records_only():
    A, B = W(1), W(2)
    w = world_with([A, B]); w.transfers += [edge(A, X1, "out")]; w.lookups[X1] = lk(5)       # A has outbound rows only
    assert sig(run(w, P3), A, "S6")[:3] == (0.0, 0.0, None)
    assert sig(run(w, P3, s6_edges=("in",)), A, "S6")[2] == "no_transfers_found"
    w2 = world_with([A, B]); w2.transfers += [edge(A, X1, "in"), edge(B, X1, "in")]; w2.lookups[X1] = lk(5)
    w2.expansions = [dict(proxy_wallet=A, direction="in"), dict(proxy_wallet=A, direction="out", gap_free=False),
                     dict(proxy_wallet=B, direction="in"), dict(proxy_wallet=B, direction="out")]
    assert sig(run(w2, P3), A, "S6")[2] == "transfers_unverified"                              # default: the out record counts
    assert sig(run(w2, P3, s6_edges=("in",)), A, "S6")[0] == 1.0                              # in mode: only the in record matters


def test_in_mode_ignores_an_outbound_share_of_a_wallet_with_inbound_rows():
    A, B = W(1), W(2)
    w = world_with([A, B])
    w.transfers += [edge(A, X2, "in"), edge(A, X1, "out"), edge(B, X1, "out")]              # A has an inbound row, so it is never NA here
    w.lookups[X1] = lk(5); w.lookups[X2] = lk(5)
    assert sig(run(w, P3), A, "S6")[0] == 1.0                                                  # default: the outbound share links A-B
    assert sig(run(w, P3, s6_edges=("in",)), A, "S6")[:3] == (0.0, 0.0, None)                # in mode: value 0, not a link, not NA
