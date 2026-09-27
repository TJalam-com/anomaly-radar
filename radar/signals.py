"""G4 signals S1–S8 (design notes r4 §3, r5 Δ1/Δ2, r6 Δ3/Δ5/Δ6 — cleared at r6 a149c645…4350; S4 anchor per r7).

Pure functions of tables already present in the DuckDB connection:
  signal_fills, markets_r, tokens_v            scoring views (radar.scoring_view)
  trades_all_any                               all-walk rows at any ts (universe only)
  scope_map(scope, condition_id)               scope definitions
  anchors(condition_id, anchor_ts, anchor_precision_s, na_reason)   S4 input (may be empty)
  wallet_profile, redeems, trade_after, transfers                   profile inputs (may be empty)
  stop_list(address)                           S6 stop-list
  bypass(proxy_wallet)                         opaque bypass list (may be empty)
Each signal writes SPARSE rows into sig_sparse; dense() assembles U(scope) x S1..S8 in SQL with per-signal
defaults (r6 Δ3 A3). NA <=> raw NULL <=> na_reason NOT NULL. Gamma fields and holders are never read here
(r4 R1/R2; tests/test_g4_guards.py greps this file with a planted positive).
"""
import json
import math
from collections import defaultdict

import pyarrow as pa

SIGNALS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
USER_SIGNED_SHAPES = ("safe_exec", "relay_hub")  # r6 Δ6 D-b, QA Q2
# default for a wallet in U with no sparse row: (raw, component, na_reason)
DEFAULTS = {"S3": ("0", "0", None), "S7": ("0", "0", None), "S4": (None, None, "no_buys"),
            "S5": (None, None, "n_resolved<min"), "S1": (None, None, "not_profiled"),
            "S2": (None, None, "not_profiled"), "S6": (None, None, "not_profiled"), "S8": (None, None, "not_profiled")}


def floorlog(x, m, c):
    """0 at or below m; log scale from m to cap c (r5 Δ1)."""
    if x is None or x <= m:
        return 0.0
    return min(1.0, max(0.0, math.log10(x / m) / math.log10(c / m)))


def floorlog_sql(x, m, c):
    return f"(CASE WHEN {x} IS NULL OR {x} <= {m} THEN 0.0 ELSE least(1.0, greatest(0.0, log10({x} / {m}) / log10({c} / {m}))) END)"


# QA ruling 2026-09-27 (post-H-001c): sums feeding a threshold, sign, rank or NA decision are exact. size round-trips
# exactly at 6 dp and price at 10 dp (measured on SNAP-003), so stake = DECIMAL(38,16) with no rounding. Thresholds are
# compared in DECIMAL; values leave as DOUBLE only for output, via dbl(): DuckDB's direct DECIMAL->DOUBLE cast is not
# correctly rounded (1.61 -> 1.6099999999999999, measured); the VARCHAR route parses the exact decimal string (correct).
# floorlog floors are continuous at the floor, so they take the dbl() value.
def dbl(x):
    return f"CAST(CAST({x} AS VARCHAR) AS DOUBLE)"


def sz(a="f"):
    return f"CAST({a}.size AS DECIMAL(38,6))"


def stake(a="f"):
    return f"(CAST({a}.size AS DECIMAL(38,6)) * CAST({a}.price AS DECIMAL(38,10)))"


def dec(x):
    return f"CAST({x} AS DECIMAL(38,16))"


def ramp_down(x, a, b):
    if x <= a:
        return 1.0
    if x >= b:
        return 0.0
    return (b - x) / (b - a)


def clip01(x):
    return min(1.0, max(0.0, x))


def poisson_binomial_tail(ps, k):
    """P(X >= k), X = sum Bernoulli(p_i). Exact DP."""
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for j, q in enumerate(dist):
            nxt[j] += q * (1 - p)
            nxt[j + 1] += q * p
        dist = nxt
    return min(1.0, max(0.0, sum(dist[k:])))


def _sparse(con, sig, rows):
    """rows: iterable of (scope, wallet, raw, component, na_reason, evidence_dict)."""
    rows = list(rows)
    if not rows:
        return
    tbl = pa.table({"scope": [r[0] for r in rows], "proxy_wallet": [r[1] for r in rows],
                    "signal_id": [sig] * len(rows),
                    # + 0.0 maps -0.0 to +0.0 (e.g. -log10(1.0)); the parquet dictionary writer treats +-0 as one value
                    # and keeps the sign of the first seen per row group, so a signed zero makes file bytes thread-dependent
                    "raw_value": pa.array([None if r[2] is None else float(r[2]) + 0.0 for r in rows], pa.float64()),
                    "component": pa.array([None if r[3] is None else float(r[3]) + 0.0 for r in rows], pa.float64()),
                    "na_reason": pa.array([r[4] for r in rows], pa.string()),
                    "evidence_json": [json.dumps(r[5], default=str) for r in rows]})
    con.register("_sp", tbl)
    con.execute("INSERT INTO sig_sparse SELECT * FROM _sp")
    con.unregister("_sp")


# ---------------------------------------------------------------- universe / prefilter

def universe(con, P, scope_set="all"):
    """U per scope; passed_prefilter = S3 raw over the scope SET >= pre_min_stake (r6 Q1); profiled = passed OR bypass."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE universe_t AS
        WITH u AS (SELECT DISTINCT s.scope, t.proxy_wallet FROM trades_all_any t JOIN scope_map s USING (condition_id)),
             pf AS (SELECT f.proxy_wallet, sum({stake()}) AS s3_set_raw
                    FROM signal_fills f JOIN scope_map s ON s.condition_id = f.condition_id AND s.scope = ?
                    WHERE f.walk = 'all' AND f.side = 'BUY' AND f.price < ? GROUP BY 1)
        SELECT u.scope, u.proxy_wallet, coalesce(pf.s3_set_raw, 0) >= {dec('?')} AS passed_prefilter,
               b.proxy_wallet IS NOT NULL AS bypass_listed
        FROM u LEFT JOIN pf USING (proxy_wallet) LEFT JOIN bypass b USING (proxy_wallet)""",
                [scope_set, P["p_low"], P["pre_min_stake"]])
    con.execute("ALTER TABLE universe_t ADD COLUMN profiled BOOLEAN")
    con.execute("UPDATE universe_t SET profiled = passed_prefilter OR bypass_listed")
    con.execute("""CREATE OR REPLACE TEMP TABLE sig_sparse(scope TEXT, proxy_wallet TEXT, signal_id TEXT,
        raw_value DOUBLE, component DOUBLE, na_reason TEXT, evidence_json TEXT)""")


# ---------------------------------------------------------------- SQL signals (whole population)

def s3(con, P):
    con.execute(f"""INSERT INTO sig_sparse
        SELECT s.scope, f.proxy_wallet, 'S3', {dbl(f'sum({stake()})')},
               {floorlog_sql(dbl(f'sum({stake()})'), P['s3_min_stake'], P['s3_cap_usdc'])}, NULL, '{{}}'
        FROM signal_fills f JOIN scope_map s USING (condition_id)
        WHERE f.walk = 'all' AND f.side = 'BUY' AND f.price < {P['p_low']} GROUP BY 1, 2""")


def s7(con, P):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE qbuys AS
        SELECT proxy_wallet, condition_id, token_id, epoch(ts) AS t FROM signal_fills
        WHERE walk = 'all' AND side = 'BUY' AND price < {P['p_low']} AND {stake('signal_fills')} >= {dec(P['s7_min_stake'])}""")
    con.execute(f"""INSERT INTO sig_sparse
        WITH per_buy AS (
            SELECT a.proxy_wallet, a.condition_id, a.t, count(DISTINCT b.proxy_wallet) AS co
            FROM qbuys a LEFT JOIN qbuys b ON a.token_id = b.token_id AND a.proxy_wallet <> b.proxy_wallet
                 AND abs(a.t - b.t) <= {P['s7_window_s']}
            GROUP BY 1, 2, 3)
        SELECT s.scope, p.proxy_wallet, 'S7', max(p.co), least(1.0, max(p.co) / {P['s7_full_at']}), NULL, '{{}}'
        FROM per_buy p JOIN scope_map s USING (condition_id) GROUP BY 1, 2""")


def s4(con, P):
    """r7: earliest-possible anchor per condition; window 0 < anchor - ts <= lead; winning token; floorlog on window stake.
    NA per (scope,wallet): every condition of the wallet in scope unusable -> reason (coarse > no_event_ts > no_winner)."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE s4_wc AS
        SELECT s.scope, f.proxy_wallet, f.condition_id,
               CASE WHEN m.void THEN 'no_winner' WHEN a.condition_id IS NULL THEN 'no_event_ts' ELSE a.na_reason END AS na,
               sum({stake()}) AS buy_stake,
               sum(CASE WHEN k.outcome_index = m.chain_winner_index
                         AND epoch(a.anchor_ts) - epoch(f.ts) > 0
                         AND epoch(a.anchor_ts) - epoch(f.ts) <= {P['s4_lead_h']} * 3600
                        THEN {stake()} ELSE 0 END) AS win_stake
        FROM signal_fills f JOIN scope_map s USING (condition_id) JOIN tokens_v k USING (token_id)
             JOIN markets_r m ON m.condition_id = f.condition_id LEFT JOIN anchors a ON a.condition_id = f.condition_id
        WHERE f.walk = 'all' AND f.side = 'BUY' GROUP BY 1, 2, 3, 4""")
    con.execute(f"""INSERT INTO sig_sparse
        WITH g AS (
            SELECT scope, proxy_wallet,
                   sum(CASE WHEN na IS NULL THEN buy_stake ELSE 0 END) AS buy,
                   sum(CASE WHEN na IS NULL THEN win_stake ELSE 0 END) AS win,
                   count(*) FILTER (WHERE na IS NULL) AS usable,
                   bool_or(na = 'event_ts_too_coarse') AS any_coarse, bool_or(na = 'no_event_ts') AS any_noevent
            FROM s4_wc GROUP BY 1, 2)
        SELECT scope, proxy_wallet, 'S4',
               CASE WHEN usable > 0 AND buy > 0 THEN {dbl('win')} / {dbl('buy')} END,
               CASE WHEN usable > 0 AND buy > 0 THEN ({dbl('win')} / {dbl('buy')}) * {floorlog_sql(dbl('win'), P['s4_min_stake'], P['s4_cap_usdc'])} END,
               CASE WHEN usable = 0 THEN (CASE WHEN any_coarse THEN 'event_ts_too_coarse' WHEN any_noevent THEN 'no_event_ts' ELSE 'no_winner' END)
                    WHEN buy <= 0 THEN 'no_buys' END,
               '{{}}'
        FROM g""")


# ---------------------------------------------------------------- S5 (SQL for NA, Python DP where n >= min)

def s5(con, P):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE s5_bets AS
        WITH b AS (
            SELECT f.proxy_wallet, f.condition_id, f.token_id,
                   sum(CASE WHEN f.side = 'BUY' THEN {sz()} ELSE -{sz()} END) AS pos,
                   {dbl(f"sum(CASE WHEN f.side = 'BUY' THEN {stake()} END)")} / nullif({dbl(f"sum(CASE WHEN f.side = 'BUY' THEN {sz()} END)")}, 0) AS entry
            FROM signal_fills f WHERE f.walk = 'all' GROUP BY 1, 2, 3)
        SELECT s.scope, b.proxy_wallet, b.entry, m.void, (k.outcome_index = m.chain_winner_index) AS won
        FROM b JOIN tokens_v k USING (token_id) JOIN markets_r m ON m.condition_id = b.condition_id
             JOIN scope_map s ON s.condition_id = b.condition_id
        WHERE b.pos > 0 AND b.entry IS NOT NULL""")
    mn = int(P["s5_min_bets"])
    con.execute(f"""INSERT INTO sig_sparse
        SELECT scope, proxy_wallet, 'S5', NULL, NULL,
               CASE WHEN count(*) FILTER (WHERE NOT void) = 0 THEN 'no_winner' ELSE 'n_resolved<min' END,
               json_object('n', count(*) FILTER (WHERE NOT void), 'void_bets', count(*) FILTER (WHERE void))::VARCHAR
        FROM s5_bets GROUP BY 1, 2 HAVING count(*) FILTER (WHERE NOT void) < {mn}""")
    cand = con.execute(f"""SELECT scope, proxy_wallet, list(entry ORDER BY entry), list(won::INT ORDER BY entry)
        FROM s5_bets WHERE NOT void GROUP BY 1, 2 HAVING count(*) >= {mn}""").fetchall()  # fetch fully: inserts below reuse con
    for i in range(0, len(cand), 5000):
        batch = cand[i:i + 5000]
        out = []
        for sc, w, entries, wons in batch:
            ps = [min(1 - 1e-6, max(1e-6, p)) for p in entries]
            k = sum(wons)
            pval = poisson_binomial_tail(ps, k)
            raw = -math.log10(max(pval, 1e-300))
            out.append((sc, w, raw, clip01(raw / P["s5_full_at"]), None, {"n": len(ps), "k": k, "pval": pval}))
        _sparse(con, "S5", out)


# ---------------------------------------------------------------- profiled-only signals (S1, S2, S6, S8)

def path_flags(con, P, edges=("in", "out")):
    """r13 Δ14 (F-4) + r14 Δ21 + r15 Δ24/Δ25 + QA C5, per profiled wallet, as of t = P["_t_asof_unix"] (live: SNAP instant).
    incomplete    : any expansion on the traced path is not (status ok AND gap-free sub-ranges AND to_block_ts >= t), or a
                    selected counterparty has no fetch record at all. Hub-capped and stop-set nodes are design exclusions,
                    never path expansions (Δ25).
    breadth_trunc : some complete path node X at hop < s6_max_hops has C(X) (pre-t counterparties; stop set, fan-out hubs,
                    hub-capped and the wallet excluded) with |C(X)| > breadth or C(X) not within F(X) (followed = selected
                    AND complete AND (child edges OR a complete empty fetch)).
    hub_capped    : number of selected counterparties on the path that were hub-capped (counted, never NA).
    hop1_in_ok    : the wallet's own hop-1 'in' expansion is complete (S1 first_funding_ts is usable only then)."""
    dirs = ",".join(f"'{d}'" for d in edges)
    if P.get("_t_asof_unix") is None:     # QA: no fail-open default (t = 0 made every to_block_ts >= t trivially true)
        raise ValueError("path_flags needs P['_t_asof_unix'] (the as-of instant t)")
    t = int(P["_t_asof_unix"])
    maxh, breadth, fan = int(P["s6_max_hops"]), int(P.get("s6_hop_breadth", 0)), int(P["s6_fanout_max"])   # v3: no breadth (hop-1 only)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE path_flags AS
        WITH prof AS (SELECT DISTINCT proxy_wallet FROM universe_t WHERE profiled),
        ex AS (SELECT e.* FROM expansions e JOIN prof USING (proxy_wallet) WHERE e.direction IN ({dirs})),
        node AS (SELECT proxy_wallet, direction, hop, address, n_logs, has_child_edges,
                        (status = 'ok' AND coalesce(gap_free, FALSE) AND coalesce(to_block_ts, 0) >= {t}) AS complete
                 FROM ex WHERE status <> 'hub'),
        hub AS (SELECT proxy_wallet, direction, hop, address FROM ex WHERE status = 'hub'),
        tr AS (SELECT t.* FROM transfers t JOIN prof USING (proxy_wallet) WHERE t.direction IN ({dirs})),
        fanout AS (SELECT counterparty FROM tr GROUP BY 1 HAVING count(DISTINCT proxy_wallet) > {fan}),
        missing AS (SELECT DISTINCT tr.proxy_wallet FROM tr
                    WHERE tr.selected AND tr.hop < {maxh}
                      AND NOT EXISTS (SELECT 1 FROM ex WHERE ex.proxy_wallet = tr.proxy_wallet AND ex.direction = tr.direction
                                      AND ex.hop = tr.hop + 1 AND ex.address = tr.counterparty)),
        cand AS (SELECT tr.proxy_wallet, tr.direction, tr.hop, tr.via, tr.counterparty, coalesce(tr.selected, FALSE) AS selected
                 FROM tr JOIN node x ON x.proxy_wallet = tr.proxy_wallet AND x.direction = tr.direction AND x.hop = tr.hop
                                      AND x.address = tr.via AND x.complete
                 WHERE tr.hop < {maxh} AND epoch(tr.first_ts) < {t} AND NOT coalesce(tr.stop_listed, FALSE)
                   AND tr.counterparty <> tr.proxy_wallet AND tr.counterparty NOT IN (SELECT address FROM stop_list)
                   AND tr.counterparty NOT IN (SELECT counterparty FROM fanout)
                   AND NOT EXISTS (SELECT 1 FROM hub h WHERE h.proxy_wallet = tr.proxy_wallet AND h.direction = tr.direction
                                   AND h.hop = tr.hop + 1 AND h.address = tr.counterparty)),
        foll AS (SELECT c.*, (c.selected AND coalesce(n.complete, FALSE) AND (coalesce(n.has_child_edges, FALSE) OR n.n_logs = 0)) AS followed
                 FROM cand c LEFT JOIN node n ON n.proxy_wallet = c.proxy_wallet AND n.direction = c.direction
                                              AND n.hop = c.hop + 1 AND n.address = c.counterparty),
        trunc AS (SELECT proxy_wallet FROM foll GROUP BY proxy_wallet, direction, hop, via
                  HAVING count(*) > {breadth} OR bool_or(NOT followed)),
        inc AS (SELECT proxy_wallet FROM node WHERE NOT complete UNION SELECT proxy_wallet FROM missing
                UNION SELECT p.proxy_wallet FROM prof p WHERE NOT EXISTS (SELECT 1 FROM ex WHERE ex.proxy_wallet = p.proxy_wallet AND ex.hop = 1))
        SELECT p.proxy_wallet,
               p.proxy_wallet IN (SELECT proxy_wallet FROM inc) AS incomplete,
               p.proxy_wallet IN (SELECT proxy_wallet FROM trunc) AS breadth_trunc,
               (SELECT count(*) FROM hub h WHERE h.proxy_wallet = p.proxy_wallet) AS hub_capped,
               coalesce((SELECT bool_and(complete) FROM node n WHERE n.proxy_wallet = p.proxy_wallet AND n.hop = 1
                         AND n.direction = 'in' AND n.address = p.proxy_wallet), FALSE) AS hop1_in_ok
        FROM prof p""")


def s1(con, P):
    rows = con.execute(f"""WITH tb AS (
            SELECT s.scope, f.proxy_wallet, min(epoch(f.ts)) AS t_bet
            FROM signal_fills f JOIN scope_map s USING (condition_id)
            WHERE f.walk = 'all' AND f.side = 'BUY' AND {stake()} >= {dec(P['s1_min_stake'])} GROUP BY 1, 2)
        SELECT u.scope, u.proxy_wallet, p.activity_status, epoch(p.first_trade_ts), epoch(p.first_funding_ts), tb.t_bet,
               p.funding_truncated, epoch(p.lower_bound_ts), coalesce(pf.hop1_in_ok, FALSE)
        FROM universe_t u LEFT JOIN wallet_profile p USING (proxy_wallet)
             LEFT JOIN tb ON tb.scope = u.scope AND tb.proxy_wallet = u.proxy_wallet
             LEFT JOIN path_flags pf ON pf.proxy_wallet = u.proxy_wallet
        WHERE u.profiled""").fetchall()
    out = []
    for sc, w, st, ftt, fft, tbet, trunc, lb_ts, hop1_ok in rows:
        if st is None or st == "unavailable":
            out.append((sc, w, None, None, "activity_unavailable", {})); continue
        if not hop1_ok:
            # r14 Δ22 / r15 Δ31: funding from an incomplete (or unrecorded) hop-1 'in' expansion is not usable.
            # Fallback t0 = first trade only when it predates the funding search window (inactive while the bound is 0).
            if ftt is not None and lb_ts is not None and ftt < lb_ts:
                cands = [ftt]
            else:
                out.append((sc, w, None, None, "transfers_unverified", {})); continue
        elif trunc:
            # QA funding-window rule (corrected): funding may predate the search window
            if ftt is not None and lb_ts is not None and ftt < lb_ts:
                cands = [ftt]            # provably old wallet -> t0 from first trade
            else:
                out.append((sc, w, None, None, "funding_window_truncated", {})); continue
        elif hop1_ok:
            cands = [x for x in (ftt, fft) if x is not None]
        if not cands:
            out.append((sc, w, None, None, "no_first_ts", {})); continue
        if tbet is None:
            out.append((sc, w, math.inf, 0.0, None, {"no_qualifying_bet": True})); continue
        hours = max(0.0, (tbet - min(cands)) / 3600)
        out.append((sc, w, hours, ramp_down(hours, P["s1_fresh_h"], P["s1_stale_h"]), None, {"clamped": tbet < min(cands)}))
    _sparse(con, "S1", out)


def s2(con, P):
    rows = con.execute(f"""WITH num AS (
            SELECT s.scope, f.proxy_wallet, {dbl(f'sum({stake()})')} AS v
            FROM signal_fills f JOIN scope_map s USING (condition_id) WHERE f.walk = 'all' GROUP BY 1, 2)
        SELECT u.scope, u.proxy_wallet, p.stats_status, p.lifetime_volume_usdc, coalesce(num.v, 0)
        FROM universe_t u LEFT JOIN wallet_profile p USING (proxy_wallet)
             LEFT JOIN num ON num.scope = u.scope AND num.proxy_wallet = u.proxy_wallet
        WHERE u.profiled""").fetchall()
    out = []
    for sc, w, st, den, num in rows:
        if st is None or st == "unavailable":
            out.append((sc, w, None, None, "stats_unavailable", {}))
        elif not den:
            out.append((sc, w, None, None, "no_lifetime_volume", {}))
        else:  # both sides top (all-walk legs, BUY+SELL) and bottom (user-stats volume_usdc) — QA Q3
            raw = num / den
            out.append((sc, w, raw, clip01(raw), None, {"num_both_sides": num, "lifetime_volume_usdc": den}))
    _sparse(con, "S2", out)


def s6(con, P, edges=("in", "out")):
    dirs = ",".join(f"'{d}'" for d in edges)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE links AS
        WITH e AS (SELECT DISTINCT t.proxy_wallet, t.counterparty FROM transfers t
                   JOIN universe_t u ON u.proxy_wallet = t.proxy_wallet AND u.profiled
                   WHERE t.direction IN ({dirs}) AND t.hop <= {P['s6_max_hops']}
                     AND t.counterparty NOT IN (SELECT address FROM stop_list)),
             fan AS (SELECT counterparty, count(DISTINCT proxy_wallet) AS n FROM e GROUP BY 1)
        SELECT e.* FROM e JOIN fan USING (counterparty) WHERE fan.n <= {P['s6_fanout_max']}""")
    rows = con.execute("""WITH pair AS (
            SELECT DISTINCT a.proxy_wallet AS w, b.proxy_wallet AS o
            FROM links a JOIN links b ON a.counterparty = b.counterparty AND a.proxy_wallet <> b.proxy_wallet),
        ne AS (SELECT proxy_wallet, count(*) AS n FROM transfers GROUP BY 1)
        SELECT u.scope, u.proxy_wallet, p.transfers_status, coalesce(ne.n, 0),
               count(DISTINCT CASE WHEN u2.profiled THEN pair.o END), bool_or(p.funding_truncated),
               coalesce(any_value(pf.incomplete), TRUE), coalesce(any_value(pf.breadth_trunc), FALSE), coalesce(any_value(pf.hub_capped), 0)
        FROM universe_t u LEFT JOIN wallet_profile p USING (proxy_wallet) LEFT JOIN ne USING (proxy_wallet)
             LEFT JOIN pair ON pair.w = u.proxy_wallet
             LEFT JOIN universe_t u2 ON u2.scope = u.scope AND u2.proxy_wallet = pair.o
             LEFT JOIN path_flags pf ON pf.proxy_wallet = u.proxy_wallet
        WHERE u.profiled GROUP BY 1, 2, 3, 4""").fetchall()
    out = []
    for sc, w, st, n_edges, linked, trunc, incomplete, btrunc, hubs in rows:
        flags = {"hub_capped": int(hubs)} if hubs else {}
        if st is None or st == "unavailable":
            out.append((sc, w, None, None, "transfers_unavailable", {}))
        elif linked == 0 and incomplete:          # a negative needs every path expansion complete (F-4); C5: wins
            out.append((sc, w, None, None, "transfers_unverified", {**flags, **({"breadth_truncated_path": True} if btrunc else {})}))
        elif linked == 0 and btrunc:              # a pre-t counterparty left unfollowed by the breadth cap (r15 Δ24)
            out.append((sc, w, None, None, "breadth_truncated", flags))
        elif not n_edges:
            out.append((sc, w, None, None, "no_transfers_found", flags))
        else:
            ev = {"edges": list(edges), **flags}
            if trunc:
                ev["edges_possibly_incomplete"] = True   # QA funding-window rule
            if linked > 0 and incomplete:
                ev["incomplete_path"] = True             # U14: found link on an incomplete path (counted, marked)
            if linked > 0 and btrunc:
                ev["breadth_truncated_path"] = True
            out.append((sc, w, float(linked), clip01(linked / P["s6_full_at"]), None, ev))
    _sparse(con, "S6", out)


def graded(linked, K):
    """params v3 s6_component graded_log: min(1, ln(1 + linked) / ln(1 + K))."""
    return min(1.0, math.log1p(linked) / math.log1p(K))


def s6_v3(con, P, edges=("in", "out")):
    """S6 v3 (params v3; D1 notes r16-r16d). t = P['_t_asof_unix'] with block(t) = P['_block_t'] (last block with ts < t):
    the SNAP instant for live scopes, T_cut(m, h) in replay cells. G = P['_s6_G'] (from the QA-recorded G record; required).
    Link edge: hop-1 transfer w<->X (in or out), first_block <= block(t), X not stop-listed. Lookup set L: X shared by >= 2 profiled
    wallets, >= 1 natural (natural = passed_prefilter in scope 'all'). Qualifying: X in L, lookup complete at t, activity_at <= G.
    linked(w) = #natural v != w sharing a qualifying X (for every scored w, bypass included; bypass-bypass never links).
    NA: transfers_unavailable > transfers_unverified (own hop-1 record incomplete) > activity_unverified (a shared lookup missing or
    incomplete) > no_transfers_found; the two 'unverified' reasons apply ONLY when the verified component < 1.0 (R16B-2 (i)); at 1.0
    the value stands, evidence incomplete_path = true, counted in n_s6_positive_on_incomplete_path.
    edges (Planner V1-1 sensitivity mode, --s6-edges in): the LINK edges and the NA/verified computation use only these directions;
    the lookup set, the lookup records and G stay the ones of the default run (G is never re-derived here)."""
    from radar import knee_g
    if "_s6_G" not in P or "_block_t" not in P or "_t_asof_unix" not in P or P["_t_asof_unix"] is None:
        raise ValueError("S6 v3 needs P['_s6_G'] (recorded G), P['_block_t'] (block(t)) and P['_t_asof_unix'] (t; no default)")
    G, bt, K, t = int(P["_s6_G"]), int(P["_block_t"]), float(P["s6_graded_k"]), int(P["_t_asof_unix"])
    # R15V3-1: one t per call and a per-wallet cache -> live scopes only; any replay scope must go through a per-cell path (not built yet)
    live_conds = {r[0] for r in con.execute("SELECT condition_id FROM markets_r").fetchall()}
    bad = [sc for (sc,) in con.execute("SELECT DISTINCT scope FROM universe_t").fetchall()
           if not (sc == "all" or sc in live_conds or sc.startswith("event:"))]
    if bad:
        raise ValueError(f"S6 v3 has no per-cell (replay) path: refusing non-live scopes {sorted(bad)[:3]}")
    prof = {w: bool(n) for w, n in con.execute(
        "SELECT proxy_wallet, bool_or(passed_prefilter) FROM universe_t WHERE scope = 'all' AND profiled GROUP BY 1").fetchall()}
    natural = {w for w, n in prof.items() if n}
    link_dirs = tuple(edges)
    all_edges = con.execute(f"""SELECT DISTINCT t.proxy_wallet, lower(t.counterparty), t.direction FROM transfers t
        WHERE t.hop = 1 AND t.direction IN ('in', 'out') AND t.first_block IS NOT NULL AND t.first_block <= {bt}
          AND lower(t.counterparty) NOT IN (SELECT lower(address) FROM stop_list)""").fetchall()
    by_x_all, by_x = defaultdict(set), defaultdict(set)
    for w, x, d in all_edges:
        if w in prof and x != w:
            by_x_all[x].add(w)                     # lookup set: both directions, whatever the edge mode (same L as the default run)
            if d in link_dirs:
                by_x[x].add(w)                     # link edges: the selected directions only
    L = {x for x, ws in by_x_all.items() if len(ws) >= 2 and ws & natural}
    recs = {}
    for a, status, capped, cap_block, from_block, to_block, subs in con.execute(
            "SELECT address, status, capped, cap_block, from_block, to_block, subranges_json FROM lookups").fetchall():
        recs[a.lower()] = {"status": status, "capped": bool(capped), "cap_block": cap_block, "from_block": from_block,
                           "to_block": to_block, "subranges": json.loads(subs or "[]"), "first_blocks": []}
    for a, fb in con.execute("SELECT address, first_block FROM lookup_y").fetchall():
        if a.lower() in recs:
            recs[a.lower()]["first_blocks"].append(fb)
    complete, qualifying, n_capped = set(), set(), 0
    for x in L:
        r = recs.get(x)
        if r is not None and knee_g.complete_at(r, bt):
            complete.add(x)
            act = knee_g.activity_at(r, bt)
            n_capped += act is None
            if act is not None and act <= G:
                qualifying.add(x)
    w_x = defaultdict(set)
    for x, ws in by_x.items():
        for w in ws:
            w_x[w].add(x)
    exp_ok = {}
    for w, d, st, gf, tbt in con.execute("SELECT proxy_wallet, direction, status, gap_free, to_block_ts FROM expansions WHERE hop = 1").fetchall():
        exp_ok.setdefault(w, {})[d] = (st == "ok" and bool(gf) and (tbt or 0) >= t)
    dl = ",".join(f"'{d}'" for d in link_dirs)
    ne = dict(con.execute(f"""SELECT proxy_wallet, count(*) FROM transfers WHERE hop = 1 AND first_block IS NOT NULL
        AND first_block <= {bt} AND direction IN ({dl}) GROUP BY 1""").fetchall())   # R15V3-5 as-of; V1-1: link directions only
    status = dict(con.execute("SELECT proxy_wallet, transfers_status FROM wallet_profile").fetchall())
    rows = con.execute("SELECT scope, proxy_wallet FROM universe_t WHERE profiled ORDER BY 1, 2").fetchall()
    out, per_w = [], {}
    for sc, w in rows:
        if w not in per_w:
            partners = set()
            unverified = False
            for x in w_x.get(w, ()):
                others = (by_x[x] & natural) - {w}
                if not others or x not in L:
                    continue
                if x in qualifying:
                    partners |= others
                elif x not in complete:
                    unverified = True
            linked = len(partners)
            comp = graded(linked, K)
            e = exp_ok.get(w, {})
            own_incomplete = not all(e.get(d) for d in link_dirs)      # V1-1: only the records of the link directions matter
            st = status.get(w)
            if st is None or st in ("unavailable", "capped"):
                res = (None, None, "transfers_unavailable", {})
            elif own_incomplete and comp < 1.0:
                res = (None, None, "transfers_unverified", {})
            elif unverified and comp < 1.0:
                res = (None, None, "activity_unverified", {})
            elif not ne.get(w):
                res = (None, None, "no_transfers_found", {})
            else:
                ev = {"rule": "C1", "G": G}
                if own_incomplete or unverified:
                    ev["incomplete_path"] = True      # value 1.0 stands (verified links saturate); marked + counted
                res = (float(linked), comp, None, ev)
            per_w[w] = res
        out.append((sc, w, *per_w[w]))
    _sparse(con, "S6", out)
    counts = defaultdict(lambda: defaultdict(int))
    for sc, w, raw, comp, na, ev in out:
        c = counts[sc]
        c["n_activity_unverified"] += na == "activity_unverified"
        c["n_transfers_unverified"] += na == "transfers_unverified"
        c["n_s6_positive"] += bool(raw)
        c["n_s6_positive_on_incomplete_path"] += bool(raw) and bool(ev.get("incomplete_path"))
    con.execute("""CREATE OR REPLACE TEMP TABLE s6v3_counts(scope TEXT, n_lookup_set BIGINT, n_lookup_complete BIGINT,
        n_lookup_incomplete BIGINT, n_capped_at_t BIGINT, n_activity_unverified BIGINT, n_transfers_unverified BIGINT,
        n_s6_positive BIGINT, n_s6_positive_on_incomplete_path BIGINT)""")
    for sc, c in sorted(counts.items()):
        con.execute("INSERT INTO s6v3_counts VALUES (?,?,?,?,?,?,?,?,?)", [sc, len(L), len(complete), len(L) - len(complete), n_capped,
                    c["n_activity_unverified"], c["n_transfers_unverified"], c["n_s6_positive"], c["n_s6_positive_on_incomplete_path"]])


def s8(con, P):
    rows = con.execute(f"""WITH wb AS (
            SELECT f.proxy_wallet, f.condition_id, f.token_id,
                   sum(CASE WHEN f.side = 'BUY' THEN {sz()} ELSE -{sz()} END) AS pos
            FROM signal_fills f JOIN universe_t u ON u.proxy_wallet = f.proxy_wallet AND u.profiled AND u.scope = 'all'
            WHERE f.walk = 'all' GROUP BY 1, 2, 3),
        winb AS (SELECT wb.proxy_wallet, wb.condition_id FROM wb JOIN tokens_v k USING (token_id)
                 JOIN markets_r m ON m.condition_id = wb.condition_id
                 WHERE wb.pos > 0 AND NOT m.void AND k.outcome_index = m.chain_winner_index),
        fr AS (SELECT proxy_wallet, condition_id, arg_min(shape, ts) AS shape, min(ts) AS rts FROM redeems GROUP BY 1, 2)
        SELECT u.scope, u.proxy_wallet, p.activity_status, epoch(p.t_snap),
               winb.condition_id, epoch(m.chain_resolution_ts), fr.shape, epoch(fr.rts), epoch(ta.next_trade_ts)
        FROM universe_t u LEFT JOIN wallet_profile p USING (proxy_wallet)
             LEFT JOIN (winb JOIN scope_map s USING (condition_id)) ON s.scope = u.scope AND winb.proxy_wallet = u.proxy_wallet
             LEFT JOIN markets_r m ON m.condition_id = winb.condition_id
             LEFT JOIN fr ON fr.proxy_wallet = u.proxy_wallet AND fr.condition_id = winb.condition_id
             LEFT JOIN trade_after ta ON ta.proxy_wallet = u.proxy_wallet AND ta.condition_id = winb.condition_id
        WHERE u.profiled""").fetchall()
    per, meta = defaultdict(list), {}
    for sc, w, st, tsnap, c, res, shape, rts, nxt in rows:
        meta[(sc, w)] = st
        if c is not None:
            per[(sc, w)].append((res, shape, rts, nxt, tsnap))
    out = []
    for (sc, w), st in meta.items():
        if st is None or st == "unavailable":
            out.append((sc, w, None, None, "activity_unavailable", {})); continue
        wins = per.get((sc, w), [])
        if not wins:
            out.append((sc, w, None, None, "no_winning_bet", {})); continue
        best, reasons = None, set()
        for res, shape, rts, nxt, tsnap in wins:
            if rts is None:
                val = 0.0  # no exit observed
            elif shape not in P.get("_user_signed_shapes", USER_SIGNED_SHAPES):
                reasons.add("auto_redeem_unknown"); continue
            else:
                if tsnap is None or tsnap < rts + P["s8_dormant_d"] * 86400:
                    reasons.add("dormancy_unobservable"); continue
                quick = 1.0 if 0 <= rts - res <= P["s8_quick_h"] * 3600 else 0.0
                dormant = 1.0 if (nxt is None or nxt > rts + P["s8_dormant_d"] * 86400) else 0.0
                val = 0.5 * quick + 0.5 * dormant
            best = val if best is None else max(best, val)
        if best is None:
            out.append((sc, w, None, None, sorted(reasons)[0], {}))
        else:
            out.append((sc, w, best, best, None, {"skipped": sorted(reasons)}))
    _sparse(con, "S8", out)


# ---------------------------------------------------------------- assembly

def compute_all(con, P, s6_edges=("in", "out")):
    """Fill sig_sparse, then create TEMP TABLE signals_dense: every (scope, wallet in U, S1..S8)."""
    universe(con, P)
    path_flags(con, P, s6_edges)
    s3(con, P); s7(con, P); s4(con, P); s5(con, P)
    s1(con, P); s2(con, P)
    s6_v3(con, P, s6_edges) if P.get("s6_rule") == "C1" else s6(con, P, s6_edges)   # params v3 -> S6 v3; v2 -> r15 S6
    s8(con, P)
    dup = con.execute("SELECT count(*) FROM (SELECT scope, proxy_wallet, signal_id FROM sig_sparse GROUP BY 1,2,3 HAVING count(*) > 1)").fetchone()[0]
    if dup:
        raise RuntimeError(f"{dup} duplicate sparse signal rows")
    cases_raw = " ".join(f"WHEN '{s}' THEN {d[0] or 'NULL'}" for s, d in DEFAULTS.items())
    cases_comp = " ".join(f"WHEN '{s}' THEN {d[1] or 'NULL'}" for s, d in DEFAULTS.items())
    cases_na = " ".join(f"WHEN '{s}' THEN {repr(d[2]) if d[2] else 'NULL'}" for s, d in DEFAULTS.items())
    sigs = ",".join(f"('{s}')" for s in SIGNALS)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE signals_dense AS
        SELECT u.scope, u.proxy_wallet, g.signal_id,
               CASE WHEN sp.signal_id IS NOT NULL THEN sp.raw_value ELSE CASE g.signal_id {cases_raw} END END AS raw_value,
               CASE WHEN sp.signal_id IS NOT NULL THEN sp.component ELSE CASE g.signal_id {cases_comp} END END AS component,
               CASE WHEN sp.signal_id IS NOT NULL THEN sp.na_reason ELSE CASE g.signal_id {cases_na} END END AS na_reason,
               coalesce(sp.evidence_json, '{{}}') AS evidence_json
        FROM universe_t u CROSS JOIN (VALUES {sigs}) g(signal_id)
             LEFT JOIN sig_sparse sp ON sp.scope = u.scope AND sp.proxy_wallet = u.proxy_wallet AND sp.signal_id = g.signal_id""")
    bad = con.execute("SELECT count(*) FROM signals_dense WHERE (raw_value IS NULL) <> (na_reason IS NOT NULL)").fetchone()[0]
    if bad:
        raise RuntimeError(f"NA invariant violated on {bad} rows (raw NULL must equal na_reason NOT NULL)")
