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

def s1(con, P):
    rows = con.execute(f"""WITH tb AS (
            SELECT s.scope, f.proxy_wallet, min(epoch(f.ts)) AS t_bet
            FROM signal_fills f JOIN scope_map s USING (condition_id)
            WHERE f.walk = 'all' AND f.side = 'BUY' AND {stake()} >= {dec(P['s1_min_stake'])} GROUP BY 1, 2)
        SELECT u.scope, u.proxy_wallet, p.activity_status, epoch(p.first_trade_ts), epoch(p.first_funding_ts), tb.t_bet,
               p.funding_truncated, epoch(p.lower_bound_ts)
        FROM universe_t u LEFT JOIN wallet_profile p USING (proxy_wallet)
             LEFT JOIN tb ON tb.scope = u.scope AND tb.proxy_wallet = u.proxy_wallet
        WHERE u.profiled""").fetchall()
    out = []
    for sc, w, st, ftt, fft, tbet, trunc, lb_ts in rows:
        if st is None or st == "unavailable":
            out.append((sc, w, None, None, "activity_unavailable", {})); continue
        if trunc:
            # QA funding-window rule (corrected): funding may predate the search window
            if ftt is not None and lb_ts is not None and ftt < lb_ts:
                cands = [ftt]            # provably old wallet -> t0 from first trade
            else:
                out.append((sc, w, None, None, "funding_window_truncated", {})); continue
        else:
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
               count(DISTINCT CASE WHEN u2.profiled THEN pair.o END), bool_or(p.funding_truncated)
        FROM universe_t u LEFT JOIN wallet_profile p USING (proxy_wallet) LEFT JOIN ne USING (proxy_wallet)
             LEFT JOIN pair ON pair.w = u.proxy_wallet
             LEFT JOIN universe_t u2 ON u2.scope = u.scope AND u2.proxy_wallet = pair.o
        WHERE u.profiled GROUP BY 1, 2, 3, 4""").fetchall()
    out = []
    for sc, w, st, n_edges, linked, trunc in rows:
        if st is None or st == "unavailable":
            out.append((sc, w, None, None, "transfers_unavailable", {}))
        elif not n_edges:
            out.append((sc, w, None, None, "no_transfers_found", {}))
        else:
            ev = {"edges": list(edges)}
            if trunc:
                ev["edges_possibly_incomplete"] = True   # QA funding-window rule
            out.append((sc, w, float(linked), clip01(linked / P["s6_full_at"]), None, ev))
    _sparse(con, "S6", out)


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
    s3(con, P); s7(con, P); s4(con, P); s5(con, P)
    s1(con, P); s2(con, P); s6(con, P, s6_edges); s8(con, P)
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
