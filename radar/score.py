"""G4 scoring run (design notes r4 §5/§6, r5 Δ2/Δ4, r6 Δ2–Δ5; cleared at r6 a149c645…4350).

python -m radar.score --snap data/snapshots/SNAP-003 --weights config/weights.toml [--prof data/profiles/PROF-001]
       [--bypass-list <file>] [--event-times <csv>] [--override S1=0 ...] [--tag t] [--shuffle-seed N] [--s6-edges in]
Every run is written to data/runs/<run_id>/ (universe, signals, scores parquet + run.json + manifest) and never
overwrites another run. weights.toml must match config/weights.lock (sha256) unless --override, which yields a
separately hashed derived config (r5 Δ4). No look-file or pass-criteria input exists in this module.
"""
import argparse
import csv
import hashlib
import io
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from radar import config, entities, scoring_view, signals
from radar.snapshots import utcnow

RUNS_DIR = config.DATA_DIR / "runs"
DUCKDB_THREADS = 1   # QA determinism ruling 2026-09-27: all scoring single-threaded; recorded in run.json


class ScoreError(RuntimeError):
    pass


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _locked(path: Path, lock: Path | None, what: str):
    raw = path.read_bytes()
    h = sha_bytes(raw)
    if lock is not None and (not lock.exists() or lock.read_text().strip() != h):
        raise ScoreError(f"{what} hash {h} does not match lock {lock} -- refusing (r4 5 / QA freeze)")
    return raw, h


def load_params(path: Path, lock: Path | None):
    """Frozen params file (config/params_frozen_*.toml): [params] for signals, [fetch] for profile/S8 shapes."""
    raw, h = _locked(path, lock, "params")
    cfg = tomllib.loads(raw.decode("utf-8"))
    P = dict(cfg["params"])
    P["_user_signed_shapes"] = tuple(cfg.get("fetch", {}).get("user_signed_redeem_shapes", signals.USER_SIGNED_SHAPES))
    P["_relay_requires_decode"] = bool(cfg.get("fetch", {}).get("relay_hub_requires_decode", True))
    return P, h, cfg


def load_weights(path: Path, lock: Path | None, overrides: dict, params_sha: str):
    raw, h = _locked(path, lock, "weights")
    cfg = tomllib.loads(raw.decode("utf-8"))
    w = {s: float(cfg["weights"].get(s, 0.0)) for s in signals.SIGNALS}
    for k, v in overrides.items():
        if k not in w:
            raise ScoreError(f"unknown signal in override: {k}")
        w[k] = float(v)
    cfg["weights"] = w
    derived_sha = h if not overrides else sha_bytes(json.dumps({"base": h, "weights": w, "params_sha256": params_sha}, sort_keys=True).encode())
    return cfg, h, derived_sha


def build_inputs(con, snap: Path, prof: Path | None, bypass: list[str], anchors, shuffle_seed, relay_requires_decode=True):
    scoring_view.build_scoring_views(con, snap)
    D = (snap / "derived").as_posix()
    con.execute(f"CREATE VIEW tokens_v AS SELECT * FROM '{D}/tokens.parquet'")
    con.execute(f"CREATE VIEW trades_all_any AS SELECT * FROM '{D}/trades.parquet' WHERE walk = 'all'")
    if shuffle_seed is not None:
        # r6 Δ5 (ii): permute ts among each condition's signal_fills rows (per walk) by a recorded seed
        con.execute(f"""CREATE OR REPLACE TABLE signal_fills_shuffled AS
            WITH base AS (SELECT *, row_number() OVER (PARTITION BY condition_id, walk ORDER BY snapshot_id, seq) AS rn FROM signal_fills),
                 perm AS (SELECT condition_id, walk, ts,
                                 row_number() OVER (PARTITION BY condition_id, walk ORDER BY hash(snapshot_id || ':' || seq::VARCHAR || ':{int(shuffle_seed)}')) AS rn
                          FROM signal_fills)
            SELECT base.* EXCLUDE (ts, rn), perm.ts FROM base JOIN perm USING (condition_id, walk, rn)""")
        con.execute("CREATE OR REPLACE VIEW signal_fills AS SELECT * FROM signal_fills_shuffled")
    events = [r[0] for r in con.execute("SELECT DISTINCT event_id FROM markets_r WHERE event_id IS NOT NULL").fetchall()]
    con.execute("""CREATE TABLE scope_map AS
        SELECT 'all' AS scope, condition_id FROM markets_r
        UNION ALL SELECT condition_id, condition_id FROM markets_r
        UNION ALL SELECT 'event:' || event_id, condition_id FROM markets_r WHERE event_id IS NOT NULL""")
    con.execute("CREATE TABLE anchors(condition_id TEXT, anchor_ts TIMESTAMPTZ, anchor_precision_s BIGINT, na_reason TEXT)")
    for c, a, p, na in anchors:
        con.execute("INSERT INTO anchors VALUES (?,?,?,?)", [c, a, p, na])
    if prof is not None:
        PD = (prof / "derived").as_posix()
        con.execute(f"""CREATE VIEW wallet_profile AS SELECT proxy_wallet, to_timestamp(first_trade_unix) AS first_trade_ts,
            to_timestamp(first_funding_unix) AS first_funding_ts, lifetime_volume_usdc, activity_status, stats_status,
            transfers_status, t_snap::TIMESTAMPTZ AS t_snap, funding_truncated, to_timestamp(lower_bound_ts_unix) AS lower_bound_ts
            FROM '{PD}/wallet_profile.parquet'""")
        s8 = prof / "s8check" / "shapes_v2.parquet"
        if s8.exists():
            # params v2: shapes re-derived in the S8 side-car (factory_direct via CREATE2; relay_hub per-tx decode)
            con.execute(f"""CREATE VIEW redeems AS SELECT r.proxy_wallet, r.condition_id, to_timestamp(r.ts_unix) AS ts,
                CASE WHEN c.shape_v2 = 'relay_hub' AND {str(bool(relay_requires_decode)).upper()} AND NOT coalesce(c.decode_ok, FALSE)
                     THEN 'relay_hub_unverified' ELSE coalesce(c.shape_v2, r.shape) END AS shape
                FROM '{PD}/redeems.parquet' r LEFT JOIN '{s8.as_posix()}' c USING (proxy_wallet, condition_id)""")
        else:
            # no side-car yet: relay_hub cannot be verified -> never counted as user-signed when decode is required
            con.execute(f"""CREATE VIEW redeems AS SELECT proxy_wallet, condition_id, to_timestamp(ts_unix) AS ts,
                CASE WHEN shape = 'relay_hub' AND {str(bool(relay_requires_decode)).upper()} THEN 'relay_hub_unverified' ELSE shape END AS shape
                FROM '{PD}/redeems.parquet'""")
        con.execute(f"CREATE VIEW trade_after AS SELECT proxy_wallet, condition_id, to_timestamp(next_trade_unix) AS next_trade_ts FROM '{PD}/trade_after.parquet'")
        con.execute(f"CREATE VIEW transfers AS SELECT proxy_wallet, direction, hop, counterparty FROM '{PD}/transfers.parquet'")
    else:
        con.execute("""CREATE TABLE wallet_profile(proxy_wallet TEXT, first_trade_ts TIMESTAMPTZ, first_funding_ts TIMESTAMPTZ,
            lifetime_volume_usdc DOUBLE, activity_status TEXT, stats_status TEXT, transfers_status TEXT, t_snap TIMESTAMPTZ,
            funding_truncated BOOLEAN, lower_bound_ts TIMESTAMPTZ)""")
        con.execute("CREATE TABLE redeems(proxy_wallet TEXT, condition_id TEXT, ts TIMESTAMPTZ, shape TEXT)")
        con.execute("CREATE TABLE trade_after(proxy_wallet TEXT, condition_id TEXT, next_trade_ts TIMESTAMPTZ)")
        con.execute("CREATE TABLE transfers(proxy_wallet TEXT, direction TEXT, hop INT, counterparty TEXT)")
    con.execute("CREATE TABLE stop_list(address TEXT)")
    con.executemany("INSERT INTO stop_list VALUES (?)", [[r[0]] for r in entities.rows()])
    con.execute("CREATE TABLE bypass(proxy_wallet TEXT)")
    if bypass:
        con.executemany("INSERT INTO bypass VALUES (?)", [[w] for w in bypass])
    return events


def score(con, weights: dict):
    """W0 rule: a signal whose component is NA for every wallet in scope 'all' gets weight 0 (r5 Δ3 step 2);
    remaining weights normalised to sum 1. Totals: Σ w·component over non-NA; NA -> 0 and counted (r4 §5)."""
    computable = {s for (s,) in con.execute(
        "SELECT signal_id FROM signals_dense WHERE scope = 'all' GROUP BY 1 HAVING count(component) > 0").fetchall()}
    w = {s: (weights[s] if s in computable else 0.0) for s in signals.SIGNALS}
    pre = sum(w.values())
    if pre <= 0:
        raise ScoreError("all applied weights are zero")
    applied = {s: v / pre for s, v in w.items()}
    cases = " ".join(f"WHEN '{s}' THEN {applied[s]!r}" for s in signals.SIGNALS)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE scores_t AS
        SELECT d.scope, d.proxy_wallet,
               list_sum(list(CASE WHEN d.component IS NOT NULL THEN (CASE d.signal_id {cases} END) * d.component ELSE 0 END
                             ORDER BY d.signal_id)) AS total_scorer,   -- fixed summation order (QA determinism ruling)
               count(*) FILTER (WHERE d.na_reason IS NOT NULL) AS n_signals_na
        FROM signals_dense d GROUP BY 1, 2""")
    con.execute("""CREATE OR REPLACE TEMP TABLE scores_final AS
        SELECT s.scope, s.proxy_wallet, s.total_scorer,
               CASE WHEN u.passed_prefilter THEN s.total_scorer END AS total_pipeline, s.n_signals_na
        FROM scores_t s JOIN universe_t u USING (scope, proxy_wallet)""")
    return applied, pre, sorted(computable)


def recompute_check(con, applied):
    """A4: totals must be reproducible from stored weights x stored components."""
    cases = " ".join(f"WHEN '{s}' THEN {applied[s]!r}" for s in signals.SIGNALS)
    bad = con.execute(f"""SELECT count(*) FROM scores_final f JOIN (
            SELECT scope, proxy_wallet, list_sum(list(coalesce((CASE signal_id {cases} END) * component, 0) ORDER BY signal_id)) AS t
            FROM signals_dense GROUP BY 1, 2) r USING (scope, proxy_wallet) WHERE abs(f.total_scorer - r.t) > 1e-12""").fetchone()[0]
    if bad:
        raise ScoreError(f"A4: {bad} totals not reproducible from stored weights")


def run(snap: Path, weights_path: Path, params_path: Path, prof: Path | None = None, bypass_path: Path | None = None,
        event_times: Path | None = None, overrides: dict | None = None, tag: str | None = None,
        shuffle_seed: int | None = None, s6_edges=("in", "out"), lock: Path | None = None, runs_dir: Path = RUNS_DIR,
        base_run_id: str | None = None, params_lock: Path | None = None, threads: int = DUCKDB_THREADS) -> dict:
    overrides = overrides or {}
    P, params_sha, _ = load_params(params_path, params_lock)
    cfg, base_sha, wsha = load_weights(weights_path, lock, overrides, params_sha)
    bypass, bmeta = [], None
    if bypass_path:
        raw = bypass_path.read_bytes()
        rd = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        col = next((c for c in (rd.fieldnames or []) if "wallet" in c.lower() or "address" in c.lower()), None)
        if col is None:
            raise ScoreError("bypass list has no wallet/address column")
        bypass = sorted({r[col].strip().lower() for r in rd if (r[col] or "").strip()})
        bmeta = {"path": str(bypass_path), "sha256": sha_bytes(raw), "rows": len(bypass)}
    anchors, emeta = [], None
    if event_times:
        from radar import events
        anchors, emeta = events.load_anchors(event_times, P["s4_lead_h"])
    con = duckdb.connect()
    con.execute("SET memory_limit='1500MB'"); con.execute(f"SET threads={int(threads)}"); con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{config.TMP_DIR.as_posix()}'")
    build_inputs(con, snap, prof, bypass, anchors, shuffle_seed, P.get("_relay_requires_decode", True))
    signals.compute_all(con, P, s6_edges=tuple(s6_edges))
    # QA provenance ruling: every signal row carries the sha256 of the WHOLE frozen params file ([params] + [fetch]),
    # so rows produced under different S8 shape rules can never share a hash (= run.json params_file_sha256)
    con.execute("ALTER TABLE signals_dense ADD COLUMN params_hash TEXT")
    con.execute("UPDATE signals_dense SET params_hash = ?", [params_sha])
    applied, pre, computable = score(con, cfg["weights"])
    recompute_check(con, applied)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{wsha[:8]}" + (f"+{tag}" if tag else "")
    out = runs_dir / run_id
    if out.exists():
        raise ScoreError(f"{out} exists — runs never overwrite")
    out.mkdir(parents=True)
    derived = []

    def export(name, q):
        p = out / f"{name}.parquet"
        con.execute(f"COPY ({q}) TO '{p.as_posix()}' (FORMAT PARQUET)")
        n = con.execute(f"SELECT count(*) FROM ({q})").fetchone()[0]
        derived.append({"file": p.name, "rows": n, "sha256": sha_bytes(p.read_bytes())})
    # memory: drop signal intermediates, then write the three gated files (sorted, largest first) before P&L tables exist
    for t in ("sig_sparse", "qbuys", "s4_wc", "s5_bets", "links", "scores_t"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    # every export ORDER BY its key: row order (and so file bytes) cannot depend on thread scheduling (determinism gate)
    export("universe", "SELECT * FROM universe_t ORDER BY scope, proxy_wallet")
    # signals (7M rows on SNAP-003): the in-memory table plus sort buffers exceed the memory cap, so spill it to a file,
    # drop the table (nothing below reads it), and sort from the file
    tmp_sig = out / "_signals_unsorted.parquet"
    con.execute(f"COPY (SELECT * FROM signals_dense) TO '{tmp_sig.as_posix()}' (FORMAT PARQUET)")
    con.execute("DROP TABLE signals_dense")
    export("signals", f"SELECT * REPLACE (raw_value + 0.0 AS raw_value, component + 0.0 AS component) FROM '{tmp_sig.as_posix()}' "
                      "ORDER BY scope, proxy_wallet, signal_id")   # + 0.0: no signed zero (parquet dictionary keeps first sign)
    tmp_sig.unlink()
    export("scores", "SELECT * REPLACE (total_scorer + 0.0 AS total_scorer, total_pipeline + 0.0 AS total_pipeline) "
                     "FROM scores_final ORDER BY scope, proxy_wallet")
    D = (snap / "derived").as_posix()
    trades_summary_q = f"""SELECT s.scope, t.walk,
            count(*) AS fills_any_ts,
            count(*) FILTER (WHERE t.ts < m.chain_resolution_ts) AS fills_before_resolution,
            {signals.dbl(f"sum({signals.sz('t')})")} AS size_shares_any_ts
        FROM '{D}/trades.parquet' t JOIN scope_map s USING (condition_id) JOIN markets_r m ON m.condition_id = t.condition_id
        GROUP BY 1, 2 ORDER BY 1, 2"""
    # contract v1.1: per (scope, walk, wallet) for EVERY wallet in U(scope), zeros included (no membership signal)
    trades_summary_wallet_q = f"""WITH agg AS (
            SELECT s.scope, t.walk, t.proxy_wallet, count(*) AS fills_any_ts,
                   count(*) FILTER (WHERE t.ts < m.chain_resolution_ts) AS fills_before_resolution, {signals.dbl(f"sum({signals.sz('t')})")} AS size_shares_any_ts
            FROM '{D}/trades.parquet' t JOIN scope_map s USING (condition_id) JOIN markets_r m ON m.condition_id = t.condition_id
            GROUP BY 1, 2, 3)
        SELECT u.scope, w.walk, u.proxy_wallet, coalesce(a.fills_any_ts, 0) AS fills_any_ts,
               coalesce(a.fills_before_resolution, 0) AS fills_before_resolution, coalesce(a.size_shares_any_ts, 0.0) AS size_shares_any_ts
        FROM universe_t u CROSS JOIN (VALUES ('all'), ('taker')) w(walk)
             LEFT JOIN agg a ON a.scope = u.scope AND a.walk = w.walk AND a.proxy_wallet = u.proxy_wallet"""
    # ---- P&L (design note r9 §2): DISPLAY ONLY, never a signal input (guard: tests/test_g4_guards.py) ----
    import glob as _glob
    fees = {}
    for gf in _glob.glob(str(snap / "raw" / "gamma" / "*" / "page0000.json")):
        for mkt in json.loads(Path(gf).read_bytes()):
            fees[mkt.get("conditionId", "").lower()] = bool(mkt.get("feesEnabled"))
    con.execute("CREATE OR REPLACE TEMP TABLE market_fees(condition_id TEXT, fees_enabled BOOLEAN)")
    if fees:
        con.executemany("INSERT INTO market_fees VALUES (?, ?)", [[k, v] for k, v in fees.items()])
    pos_q = (f"SELECT proxy_wallet, condition_id, sum(CAST(total_pnl AS DECIMAL(38,6))) AS positions_total_pnl FROM '{D}/positions_snap.parquet' GROUP BY 1, 2"
             if (snap / "derived" / "positions_snap.parquet").exists()
             else "SELECT NULL::TEXT AS proxy_wallet, NULL::TEXT AS condition_id, NULL::DECIMAL(38,6) AS positions_total_pnl WHERE FALSE")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pnl_market AS
        WITH legs AS (
            SELECT t.proxy_wallet, t.condition_id, t.token_id,
                   sum(CASE WHEN t.side = 'SELL' THEN {signals.stake('t')} ELSE 0 END) AS cash_out,
                   sum(CASE WHEN t.side = 'BUY' THEN {signals.stake('t')} ELSE 0 END) AS cash_in,
                   sum(CASE WHEN t.side = 'BUY' THEN {signals.sz('t')} ELSE -{signals.sz('t')} END) AS net_shares
            FROM '{D}/trades.parquet' t WHERE t.walk = 'all' GROUP BY 1, 2, 3),   -- ALL timestamps: post-resolution fills are cash flows
        pos AS ({pos_q})
        SELECT l.proxy_wallet, l.condition_id, m.void,
               sum(l.cash_in) AS cash_in, sum(l.cash_out) AS cash_out,
               sum(CASE WHEN k.outcome_index = m.chain_winner_index THEN l.net_shares ELSE 0 END) AS payout,
               CASE WHEN m.void THEN NULL ELSE sum(l.cash_out) - sum(l.cash_in)
                    + sum(CASE WHEN k.outcome_index = m.chain_winner_index THEN l.net_shares ELSE 0 END) END AS pnl_trades,
               any_value(p.positions_total_pnl) AS positions_total_pnl,
               bool_or(l.net_shares < 0) AS negative_net_shares,
               coalesce(any_value(f.fees_enabled), NULL) AS fees_enabled
        FROM legs l JOIN tokens_v k USING (token_id) JOIN markets_r m ON m.condition_id = l.condition_id
             LEFT JOIN pos p ON p.proxy_wallet = l.proxy_wallet AND p.condition_id = l.condition_id
             LEFT JOIN market_fees f ON f.condition_id = l.condition_id
        GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE pnl_scope AS
        SELECT u.scope, u.proxy_wallet,
               count(pm.condition_id) FILTER (WHERE NOT pm.void) AS n_markets,
               count(pm.condition_id) FILTER (WHERE pm.void) AS n_void_excluded,
               coalesce(sum(pm.cash_in) FILTER (WHERE NOT pm.void), 0) AS cash_in,
               coalesce(sum(pm.cash_out) FILTER (WHERE NOT pm.void), 0) AS cash_out,
               coalesce(sum(pm.payout) FILTER (WHERE NOT pm.void), 0) AS payout,
               coalesce(sum(pm.pnl_trades) FILTER (WHERE NOT pm.void), 0) AS pnl_trades,
               sum(pm.positions_total_pnl) FILTER (WHERE NOT pm.void) AS positions_total_pnl,
               coalesce(bool_or(pm.negative_net_shares) FILTER (WHERE NOT pm.void), FALSE) AS flag_negative_net_shares,
               coalesce(bool_or(pm.fees_enabled) FILTER (WHERE NOT pm.void), FALSE) AS flag_fees_not_modelled,
               'unchecked' AS flag_position_changes_outside_trades
        FROM universe_t u LEFT JOIN (pnl_market pm JOIN scope_map s USING (condition_id))
             ON s.scope = u.scope AND pm.proxy_wallet = u.proxy_wallet
        GROUP BY 1, 2""")
    # exact DECIMAL internally; the run-dir contract keeps DOUBLE columns (one conversion, at output)
    pnl_dbl = ", ".join(f"{signals.dbl(c)} AS {c}" for c in ("cash_in", "cash_out", "payout", "pnl_trades", "positions_total_pnl"))
    for name, q in (("pnl", f"SELECT * REPLACE ({pnl_dbl}) FROM pnl_scope ORDER BY scope, proxy_wallet"),
                    ("pnl_market", f"SELECT * REPLACE ({pnl_dbl}) FROM pnl_market ORDER BY proxy_wallet, condition_id"),
                    ("trades_summary", trades_summary_q),
                    ("trades_summary_wallet", trades_summary_wallet_q + " ORDER BY u.scope, w.walk, u.proxy_wallet")):
        export(name, q)
    snap_sha = sha_bytes((snap / "manifest.json").read_bytes())
    step4_sha = sha_bytes((snap / "step4" / "manifest.json").read_bytes())
    runrec = {
        "contract": "run-dir v1.2 (design note r9)", "run_id": run_id, "tag": tag, "base_run_id": base_run_id,
        "started_at": stamp, "finished_at": utcnow(),
        "weights_path": str(weights_path), "weights_file_sha256": base_sha, "weights_sha256": wsha,
        "override_json": overrides, "weights_applied": applied, "weights_pre_norm_sum": pre, "computable_signals": computable,
        "weights_applied_json": {**applied, "pre_norm_sum": pre},
        "params_path": str(params_path), "params_file_sha256": params_sha,
        "params_json": {**{k: v for k, v in P.items() if not k.startswith("_")}, "user_signed_redeem_shapes": list(P["_user_signed_shapes"]), "relay_hub_requires_decode": P["_relay_requires_decode"], "perturbation": ({"type": "shuffle_ts", "seed": shuffle_seed, "unit": "condition,walk"} if shuffle_seed is not None else None),
                        "s6_edges": list(s6_edges)},
        "snapshots": [{"snap": snap.name, "manifest_sha256": snap_sha}, {"step4_manifest_sha256": step4_sha}],
        "snapshots_json": [{"id": snap.name, "manifest_sha256": snap_sha},
                           {"id": f"{snap.name}/step4", "manifest_sha256": step4_sha}],
        "profile": ({"path": str(prof), "manifest_sha256": sha_bytes((prof / "manifest.json").read_bytes())} if prof else None),
        "event_times": emeta, "bypass_list": bmeta,
        "event_times_sha256": (emeta or {}).get("sha256"), "bypass_list_sha256": (bmeta or {}).get("sha256"),
        "mode": {"shuffle_seed": shuffle_seed, "s6_edges": list(s6_edges), "override": bool(overrides)},
        "prefilter_rule": f"S3 raw over scope 'all' (sum of BUY stake at price < {P['p_low']}, fills before resolution) >= {P['pre_min_stake']} USDC",
        "prefilter_cut": con.execute("""SELECT json_object('U', count(*), 'passed', count(*) FILTER (WHERE passed_prefilter),
                 'bypass_listed', count(*) FILTER (WHERE bypass_listed), 'profiled', count(*) FILTER (WHERE profiled))::VARCHAR
                 FROM universe_t WHERE scope = 'all'""").fetchone()[0],
        "scope_limit_text": f"wallets without >= {P['pre_min_stake']} USDC of buys below {P['p_low']} were not profiled (S1/S2/S6/S8 = NA)",
        "derived": derived,
        "duckdb_threads": con.execute("SELECT current_setting('threads')").fetchone()[0],
        "numerics": "exact DECIMAL sums: size DECIMAL(38,6), price DECIMAL(38,10), stake DECIMAL(38,16); DOUBLE at output",
    }
    body = json.dumps(runrec, indent=1, sort_keys=True, default=str).encode()
    (out / "run.json").write_bytes(body)
    return {"run_id": run_id, "dir": str(out), "run_json_sha256": sha_bytes(body), "derived": derived,
            "weights_applied": applied, "computable": computable, "prefilter_cut": runrec["prefilter_cut"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--params", required=True, help="frozen params file (config/params_frozen_*.toml)")
    ap.add_argument("--params-lock", default=None, help="default: <params>.lock")
    ap.add_argument("--lock", default=None, help="weights lock file (default: <weights>.lock)")
    ap.add_argument("--prof")
    ap.add_argument("--bypass-list")
    ap.add_argument("--event-times")
    ap.add_argument("--override", action="append", default=[], help="S1=0 (repeatable)")
    ap.add_argument("--tag")
    ap.add_argument("--base-run")
    ap.add_argument("--shuffle-seed", type=int)
    ap.add_argument("--s6-edges", default="in,out")
    ap.add_argument("--threads", type=int, default=DUCKDB_THREADS, help="DuckDB threads (gate run C uses 4; default 1)")
    ap.add_argument("--runs-dir", default=None)
    a = ap.parse_args(argv)
    ov = dict(o.split("=", 1) for o in a.override)
    if (ov or a.shuffle_seed is not None or a.s6_edges != "in,out") and not a.tag:
        ap.error("perturbed/override runs need --tag (r5 Δ4)")
    w = Path(a.weights)
    pp = Path(a.params)
    out = run(Path(a.snap), w, pp, Path(a.prof) if a.prof else None, Path(a.bypass_list) if a.bypass_list else None,
              Path(a.event_times) if a.event_times else None, ov, a.tag, a.shuffle_seed, tuple(a.s6_edges.split(",")),
              Path(a.lock) if a.lock else w.with_suffix(w.suffix + ".lock"), base_run_id=a.base_run,
              params_lock=Path(a.params_lock) if a.params_lock else pp.with_suffix(pp.suffix + ".lock"),
              runs_dir=Path(a.runs_dir) if a.runs_dir else RUNS_DIR, threads=a.threads)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
