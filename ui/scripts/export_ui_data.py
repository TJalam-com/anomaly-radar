"""Anomaly Radar UI data export (G6 r6). Reads ONE scored run dir (read-only) + its SNAP, writes the UI's JSON.

Data-layer blinding (G6 U7 / O5): every query below names its columns explicitly, and FORBIDDEN columns can never be
selected (guard() raises; tests/test_export.py plants a violation). The written JSON therefore physically lacks
total_scorer, profiled, bypass_listed and bypass counts. Paths written are relative or hashes only (U3/U13).

usage: python export_ui_data.py <run_dir> <snap_dir> <out_dir> [--profile-label TEXT]
"""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import duckdb

FORBIDDEN = ("total_scorer", "profiled", "bypass_listed", "bypass_list", "bypass")
SIGNALS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
UNVERIFIED = {"on": False}   # set per export: profile writer unverified (PROF-001) -> every S6 > 0 is on an incomplete path


def s6_incomplete(raw, ev) -> bool:
    """U14/C0-12 (F-4): a positive S6 rests on an incomplete path if the scorer flagged it (r15 incomplete_path; PROF-001-era
    edges_possibly_incomplete) or if the profile writer is unverified (no fetch records at all)."""
    if raw is None or raw <= 0:
        return False
    ev = ev or {}
    return bool(UNVERIFIED["on"] or ev.get("incomplete_path") or ev.get("edges_possibly_incomplete"))


class ExportError(RuntimeError):
    pass


def guard(sql: str) -> str:
    """Refuse any query that mentions a forbidden column (word match; 'passed_prefilter' is allowed)."""
    low = sql.lower()
    for f in FORBIDDEN:
        if re.search(rf"(?<![a-z_]){re.escape(f)}(?![a-z_])", low):
            raise ExportError(f"UI export query selects forbidden column {f!r} (G6 U7)")
    if re.search(r"select\s+\*", low) or re.search(r"\*\s+replace", low):
        raise ExportError("UI export queries must name columns (no SELECT *)")
    return sql


def q(con, sql, params=None):
    return con.execute(guard(sql), params or []).fetchall()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def export(run_dir: Path, snap: Path, out: Path, profile_label: str, banner: str, run_label: str, profile_unverified: bool = False) -> dict:
    UNVERIFIED["on"] = bool(profile_unverified)
    rj = json.loads((run_dir / "run.json").read_bytes())
    gate = run_dir / "gate.json"
    gate_pass = json.loads(gate.read_bytes())["pass"] if gate.exists() else None
    con = duckdb.connect()
    con.execute("SET threads=1")
    R = run_dir.as_posix(); D = (snap / "derived").as_posix(); S4 = (snap / "step4" / "resolutions_chain.parquet").as_posix()
    # ---- leaderboard, scope 'all': pipeline totals only (U6)
    rows = q(con, f"""SELECT s.proxy_wallet, s.total_pipeline, s.n_signals_na, u.passed_prefilter
        FROM '{R}/scores.parquet' s JOIN '{R}/universe.parquet' u USING (scope, proxy_wallet)
        WHERE s.scope = 'all' AND s.total_pipeline IS NOT NULL ORDER BY s.total_pipeline DESC, s.proxy_wallet""")
    n_universe = q(con, f"SELECT count(*) FROM '{R}/universe.parquet' WHERE scope = 'all'")[0][0]
    n_pass = q(con, f"SELECT count(*) FROM '{R}/universe.parquet' WHERE scope = 'all' AND passed_prefilter")[0][0]
    wallets = [r[0] for r in rows]
    con.execute("CREATE TEMP TABLE lw(proxy_wallet TEXT)")
    con.executemany("INSERT INTO lw VALUES (?)", [[w] for w in wallets])
    sig = {}
    for w, sid, raw, comp, na, ev in q(con, f"""SELECT g.proxy_wallet, g.signal_id, g.raw_value, g.component, g.na_reason, g.evidence_json
            FROM '{R}/signals.parquet' g JOIN lw USING (proxy_wallet) WHERE g.scope = 'all'"""):
        sig.setdefault(w, {})[sid] = {"raw": raw, "component": comp, "na_reason": na, "evidence": json.loads(ev or "{}")}
    # markets hit: distinct conditions with walk-'all' legs before chain resolution (pre-resolution legs)
    hits = dict(q(con, f"""SELECT t.proxy_wallet, count(DISTINCT t.condition_id) FROM '{D}/trades.parquet' t
        JOIN lw USING (proxy_wallet) JOIN '{S4}' r ON r.condition_id = t.condition_id
        WHERE t.walk = 'all' AND epoch(t.ts) < r.resolution_ts_unix GROUP BY 1"""))
    board = []
    for rank, (w, tp, nna, pp) in enumerate(rows, 1):
        s = sig.get(w, {})
        board.append({"rank": rank, "wallet": w, "score": tp, "n_signals_na": nna, "prefilter_pass": bool(pp),
                      "signals": {k: {"component": s.get(k, {}).get("component"), "raw": s.get(k, {}).get("raw"),
                                      "na_reason": s.get(k, {}).get("na_reason"),
                                      # U14/C0-12: S6 positive resting on an incomplete path (r13 F-4 flag; PROF-001 era key too)
                                      **({"incomplete_path": s6_incomplete(s.get(k, {}).get("raw"), s.get(k, {}).get("evidence"))}
                                         if k == "S6" else {})} for k in SIGNALS},
                      "s3_low_odds_stake_usdc": s.get("S3", {}).get("raw"), "markets_hit": hits.get(w, 0)})
    # ---- run context (U3): hashes and relative ids only
    snaps = [{"id": x["id"].replace("\\", "/").split("/")[-2] + "/" + x["id"].replace("\\", "/").split("/")[-1]
              if "/" in x["id"].replace("\\", "/") else x["id"], "manifest_sha256": x["manifest_sha256"]} for x in rj["snapshots_json"]]
    ctx = {"run_id": rj["run_id"], "started_at": rj["started_at"], "finished_at": rj["finished_at"],
           "weights_sha256": rj["weights_sha256"], "weights_applied": {k: rj["weights_applied"][k] for k in SIGNALS},
           "params_file_sha256": rj["params_file_sha256"], "snapshots": snaps,
           "event_times_sha256": rj.get("event_times_sha256"), "prefilter_rule": rj["prefilter_rule"],
           "scope_limit_text": rj["scope_limit_text"], "duckdb_threads": rj.get("duckdb_threads"),
           "determinism_gate_pass": gate_pass, "run_json_sha256": sha(run_dir / "run.json"),
           "profile_label": profile_label, "s5_min_bets": rj["params_json"].get("s5_min_bets"),
           "banner": banner, "run_label": run_label, "fixture": False,
           "weights_file_sha256": rj.get("weights_file_sha256"), "weights_version": _weights_version(rj),
           "g5_passed_weights_sha256": None, "g5_report_sha256": None}
    out.mkdir(parents=True, exist_ok=True)
    extra = export_wallets_and_markets(con, R, D, S4, out, wallets)
    extra["market_view_files"] = export_market_views(con, R, D, S4, out, rj["params_json"])
    extra["link_files"], ctx["links"] = export_links(con, rj, out, sig, {w: tp for w, tp, _, _ in rows})
    files = {"context.json": ctx, "markets.json": extra["markets"],
             "leaderboard_all.json": {"scope": "all", "n_ranked": len(board), "n_universe": n_universe, "n_prefilter_pass": n_pass,
                                      "rows": board}}
    manifest = {"source_run_id": rj["run_id"], "source_run_json_sha256": ctx["run_json_sha256"], "files": {}}
    for name, obj in files.items():
        body = dumps(obj)
        low = body.lower()
        # total_scorer / bypass*: nowhere. profiled: never as a key/field (the word appears in the G6 §2a NA text
        # "not profiled (below prefilter)" and in run.json scope_limit_text, both required; P3 vs §2a raised with QA)
        if b"total_scorer" in low or b"bypass" in low or re.search(rb'"profiled"\s*:', low):
            raise ExportError(f"forbidden column in {name}")
        if b"insider" in low:
            raise ExportError(f"banned word in {name}")
        (out / name).write_bytes(body)
        manifest["files"][name] = hashlib.sha256(body).hexdigest()
    manifest["wallet_files"] = extra["wallet_files"]
    manifest["price_files"] = extra["price_files"]
    manifest["market_view_files"] = extra["market_view_files"]
    manifest["link_files"] = extra["link_files"]
    (out / "manifest.json").write_bytes(json.dumps(manifest, indent=1, sort_keys=True).encode())
    return {k: v for k, v in manifest.items() if k not in ("wallet_files", "price_files", "market_view_files")} | {
        "n_wallet_files": len(extra["wallet_files"]), "n_price_files": len(extra["price_files"]),
        "n_market_view_files": len(extra["market_view_files"]), "n_link_files": len(extra["link_files"])}


def _weights_version(rj) -> str | None:
    """version string of the weights file the run used (e.g. DEV-equal-not-frozen); read by path from run.json."""
    import tomllib
    try:
        return tomllib.loads(Path(rj["weights_path"]).read_text(encoding="utf-8")).get("version")
    except (OSError, KeyError, ValueError):
        return None


def _event_titles(gamma_dir: Path) -> dict:
    """condition_id -> Gamma event title, from the raw Gamma market pages of the snapshot (public API data)."""
    out = {}
    for f in sorted(gamma_dir.glob("*/page0000.json")):
        for m in json.loads(f.read_bytes()):
            ev = m.get("events") or []
            if ev and m.get("conditionId"):
                out[m["conditionId"].lower()] = ev[0].get("title")
    return out


def _finite(o):
    """non-finite floats (S1 raw = inf for "no qualifying bet") -> null: the UI parses strict JSON (JSON.parse), which
    rejects Infinity/NaN. The meaning stays in the evidence (no_qualifying_bet) and the UI renders null as such."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def dumps(obj) -> bytes:
    return json.dumps(_finite(obj), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write(path: Path, obj) -> str:
    body = dumps(obj)
    low = body.lower()
    if b"total_scorer" in low or b"bypass" in low or re.search(rb'"profiled"\s*:', low) or b"insider" in low:
        raise ExportError(f"forbidden content in {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


APP_DIR = Path(__file__).resolve().parents[2]          # app/ (radar package: the scorer's stop list)


def export_links(con, rj, out: Path, sig: dict, scores: dict):
    """V4 ego view (QA ruling (b)-lite): per profiled wallet, its S6 linked wallets and the data to list, per link, the
    shared counterparties with hop and direction. No transitive grouping. The relation is rebuilt here from the run's
    profile bundle with the scorer's rule (hop <= s6_max_hops, in+out, stop list, fan-out <= s6_fanout_max) and must
    reproduce S6 raw exactly for every computed wallet (else ExportError): two mechanisms, one number.
    Storage: links/<w>.json = linked [[o, n_shared, score_o]] + the wallet's OWN shared counterparties [[c, dir, hop]]; the UI
    intersects two wallets' lists for a link's complete shared set (2.6 M link x counterparty rows are never duplicated)."""
    prof = rj.get("profile") or {}
    if not prof.get("path"):
        return {}, {"available": False, "reason": "this run used no wallet profiles"}
    pdir = Path(prof["path"])
    if sha(pdir / "manifest.json") != prof["manifest_sha256"]:
        raise ExportError("profile manifest hash differs from run.json: refusing to export links")
    PD = (pdir / "derived").as_posix()
    P = rj["params_json"]
    sys.path.insert(0, str(APP_DIR))
    from radar import entities
    stop = sorted({r[0].lower() for r in entities.rows()})
    con.execute("CREATE TEMP TABLE lk_stop(address TEXT)")
    con.executemany("INSERT INTO lk_stop VALUES (?)", [[a] for a in stop])
    pw = sorted(w for w, s in sig.items() if (s.get("S6") or {}).get("na_reason") != "not_profiled")
    con.execute("CREATE TEMP TABLE lk_w(w TEXT)")
    con.executemany("INSERT INTO lk_w VALUES (?)", [[w] for w in pw])
    con.execute(guard(f"""CREATE TEMP TABLE lk_e AS SELECT t.proxy_wallet AS w, t.counterparty AS c, t.direction AS d, min(t.hop) AS h
        FROM '{PD}/transfers.parquet' t JOIN lk_w ON lk_w.w = t.proxy_wallet
        WHERE t.direction IN ('in', 'out') AND t.hop <= {int(P['s6_max_hops'])} AND t.counterparty NOT IN (SELECT address FROM lk_stop)
        GROUP BY 1, 2, 3"""))
    con.execute(guard(f"""CREATE TEMP TABLE lk_k AS SELECT e.w, e.c, e.d, e.h FROM lk_e e JOIN (SELECT c FROM lk_e GROUP BY c
        HAVING count(DISTINCT w) BETWEEN 2 AND {int(P['s6_fanout_max'])}) f USING (c)"""))
    linked = {}
    for w, o, n in q(con, """SELECT a.w, b.w, count(DISTINCT a.c) FROM lk_k a JOIN lk_k b ON a.c = b.c AND a.w <> b.w
                            GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2"""):
        linked.setdefault(w, []).append([o, n, scores.get(o)])
    cps = {}
    for w, c, d, h in q(con, "SELECT w, c, d, h FROM lk_k ORDER BY w, c, d"):
        cps.setdefault(w, []).append([c, d, h])
    bad = []
    for w in pw:
        s6 = sig[w].get("S6") or {}
        if s6.get("na_reason") is None and int(s6.get("raw") or 0) != len(linked.get(w, [])):
            bad.append((w, s6.get("raw"), len(linked.get(w, []))))
        if s6.get("na_reason") is not None and linked.get(w):
            bad.append((w, s6.get("na_reason"), len(linked[w])))
    if bad:
        raise ExportError(f"link rebuild disagrees with S6 raw for {len(bad)} wallets, e.g. {bad[:3]}")
    files = {}
    for w in pw:
        s6 = sig[w].get("S6") or {}
        if s6.get("na_reason") is not None:
            continue
        files[w] = _write(out / "links" / f"{w}.json", {
            "wallet": w, "n_linked": len(linked.get(w, [])), "linked": linked.get(w, []), "cps": cps.get(w, []),
            "incomplete": s6_incomplete(s6.get("raw"), s6.get("evidence"))})
    meta = {"available": True, "max_hops": int(P["s6_max_hops"]), "fanout_max": int(P["s6_fanout_max"]), "edges": ["in", "out"],
            "stop_list": stop, "stop_list_sha256": hashlib.sha256("\n".join(stop).encode()).hexdigest(),
            "rule": (f"two profiled wallets are linked when they share a counterparty within {int(P['s6_max_hops'])} hops (in or out), "
                     f"excluding the stop list and counterparties shared by more than {int(P['s6_fanout_max'])} profiled wallets"),
            "n_pairs": sum(len(v) for v in linked.values()) // 2}
    return files, meta


TOP_N = 150   # G6 O8 QA default: graph shows top-N by total_pipeline, "showing N of M"; the DOM table is complete


def export_market_views(con, R, D, S4, out: Path, P: dict) -> dict:
    """V2 per market: header, volumes (taker walk; Gamma only as cross-check), bubble nodes = wallets with rebuilt
    exact net > 0 at T_ref (all of them, for the DOM table), top-N for the graph, S7 co-buy edges among the top-N."""
    files = {}
    vol = {c: (s, u, n) for c, s, u, n in q(con, f"""SELECT condition_id,
            CAST(CAST(sum(CAST(size AS DECIMAL(38,6))) AS VARCHAR) AS DOUBLE),
            CAST(CAST(sum(CAST(size AS DECIMAL(38,6)) * CAST(price AS DECIMAL(38,10))) AS VARCHAR) AS DOUBLE), count(*)
        FROM '{D}/trades.parquet' WHERE walk = 'taker' GROUP BY 1""")}
    post = dict(q(con, f"""SELECT t.condition_id, count(*) FROM '{D}/trades.parquet' t JOIN '{S4}' r USING (condition_id)
        WHERE t.walk = 'taker' AND epoch(t.ts) >= r.resolution_ts_unix GROUP BY 1"""))
    gamma = dict(q(con, f"SELECT condition_id, gamma_volume_shares FROM '{D}/markets.parquet'"))
    holders = dict(q(con, f"""SELECT k.condition_id, count(DISTINCT h.proxy_wallet) FROM '{D}/holders_snap.parquet' h
        JOIN '{D}/tokens.parquet' k USING (token_id) GROUP BY 1"""))
    nodes = {}
    for c, w, oi, net in q(con, f"""WITH p AS (
            SELECT t.condition_id, t.proxy_wallet, k.outcome_index,
                   sum(CASE WHEN t.side = 'BUY' THEN CAST(t.size AS DECIMAL(38,6)) ELSE -CAST(t.size AS DECIMAL(38,6)) END) AS net
            FROM '{D}/trades.parquet' t JOIN '{D}/tokens.parquet' k USING (token_id) JOIN '{S4}' r ON r.condition_id = t.condition_id
            WHERE t.walk = 'all' AND epoch(t.ts) < r.t_ref_unix GROUP BY 1, 2, 3)
        SELECT condition_id, proxy_wallet, outcome_index, CAST(CAST(net AS VARCHAR) AS DOUBLE) FROM p WHERE net > 0
        ORDER BY 1, 2, 3"""):
        nodes.setdefault(c, {}).setdefault(w, {})[str(oi)] = net
    sc = {}
    for c, w, tp, nna, pp in q(con, f"""SELECT s.scope, s.proxy_wallet, s.total_pipeline, s.n_signals_na, u.passed_prefilter
            FROM '{R}/scores.parquet' s JOIN '{R}/universe.parquet' u USING (scope, proxy_wallet)
            WHERE s.scope NOT IN ('all') AND s.scope NOT LIKE 'event:%'"""):
        sc[(c, w)] = (tp, nna, bool(pp))
    # S7 qualifying buys (same definition as signals.s7: before resolution, walk all, BUY, price < p_low, exact stake >= min)
    con.execute(guard(f"""CREATE OR REPLACE TEMP TABLE qb AS SELECT t.condition_id, t.token_id, t.proxy_wallet, epoch(t.ts) AS ts
        FROM '{D}/trades.parquet' t JOIN '{S4}' r ON r.condition_id = t.condition_id
        WHERE t.walk = 'all' AND t.side = 'BUY' AND epoch(t.ts) < r.resolution_ts_unix AND t.price < {float(P['p_low'])}
          AND CAST(t.size AS DECIMAL(38,6)) * CAST(t.price AS DECIMAL(38,10)) >= CAST({float(P['s7_min_stake'])} AS DECIMAL(38,16))"""))
    edges_all = {}
    for c, a, b in q(con, f"""SELECT DISTINCT x.condition_id, least(x.proxy_wallet, y.proxy_wallet), greatest(x.proxy_wallet, y.proxy_wallet)
            FROM qb x JOIN qb y ON x.token_id = y.token_id AND x.proxy_wallet <> y.proxy_wallet AND abs(x.ts - y.ts) <= {int(P['s7_window_s'])}
            ORDER BY 1, 2, 3"""):
        edges_all.setdefault(c, []).append((a, b))
    mk = dict(q(con, f"SELECT condition_id, question FROM '{D}/markets.parquet'"))
    s6pos = {(sc_, w_): s6_incomplete(raw_, json.loads(ev_ or "{}")) for sc_, w_, raw_, ev_ in q(con, f"""SELECT scope, proxy_wallet, raw_value,
            evidence_json FROM '{R}/signals.parquet' WHERE signal_id = 'S6' AND raw_value > 0 AND scope NOT IN ('all') AND scope NOT LIKE 'event:%'""")}
    for c in sorted(mk):
        ns = []
        for w, pos in sorted(nodes.get(c, {}).items()):
            tp, nna, pp = sc.get((c, w), (None, None, None))
            ns.append({"wallet": w, "shares_by_outcome": pos, "shares": sum(pos.values()), "score": tp, "n_signals_na": nna,
                       "prefilter_pass": pp, "s6_incomplete": s6pos.get((c, w), False)})
        ranked = sorted([n for n in ns if n["score"] is not None], key=lambda n: (-n["score"], n["wallet"]))
        top = {n["wallet"] for n in ranked[:TOP_N]}
        es = [{"a": a, "b": b, "type": "co-timing (S7)"} for a, b in edges_all.get(c, []) if a in top and b in top]
        s, u, n = vol.get(c, (0.0, 0.0, 0))
        files[c] = _write(out / "market_views" / f"{c}.json", {
            "condition_id": c, "volume_taker_shares": s, "volume_taker_usdc": u, "taker_rows": n,
            "gamma_volume_shares_crosscheck": gamma.get(c), "api_holders_count_crosscheck": holders.get(c, 0),
            "post_resolution_taker_fills": post.get(c, 0), "node_rule": "wallets with net shares > 0 at T_ref, rebuilt from trades (walk all, exact)",
            "node_size": "net shares held at T_ref (sum over outcomes held)", "top_n": TOP_N, "n_nodes": len(ns),
            "n_graph_nodes": len(top), "graph_rule": "top-N by anomalous pattern score at this market's scope (scored wallets only)",
            "nodes": ns, "graph_wallets": sorted(top), "edges": es, "n_s7_pairs_total": len(edges_all.get(c, [])),
            "s6_edges_note": "no S6 links in this run (no profile inputs)"})
    return files


def export_wallets_and_markets(con, R, D, S4, out: Path, wallets: list) -> dict:
    """V3 wallet pages (ranked wallets) + market metadata + per-market price lines (V2/V3)."""
    mk = q(con, f"""SELECT m.condition_id, m.question, m.event_id, m.neg_risk, epoch(m.closed_at)::BIGINT,
            r.resolution_ts_unix, r.resolution_tx, r.void, r.chain_winner_index, r.t_ref_unix, r.closed_at_delta_s
        FROM '{D}/markets.parquet' m LEFT JOIN '{S4}' r USING (condition_id) ORDER BY m.condition_id""")
    markets = {c: {"question": qn, "event_id": ev, "neg_risk": bool(nr), "closed_at_unix": ca, "resolution_ts_unix": rts,
                   "resolution_tx": rtx, "void": bool(v) if v is not None else None, "winner_index": wi, "t_ref_unix": tr,
                   "closed_at_delta_s": cd} for c, qn, ev, nr, ca, rts, rtx, v, wi, tr, cd in mk}
    outcomes = {}
    for c, i, o in q(con, f"SELECT condition_id, outcome_index, outcome FROM '{D}/tokens.parquet'"):
        outcomes.setdefault(c, {})[str(i)] = o
    ev_titles = _event_titles(Path(D).parent / "raw" / "gamma")
    for c in markets:
        markets[c]["outcomes"] = outcomes.get(c, {})
        markets[c]["event_title"] = ev_titles.get(c)
    # price lines: 1 h VWAP per outcome from API taker-walk fills (each fill once), USDC per share
    price_files = {}
    series = {}
    for c, i, h, vw, n in q(con, f"""SELECT t.condition_id, k.outcome_index, (epoch(t.ts)::BIGINT // 3600) * 3600 AS h,
            sum(t.size * t.price) / sum(t.size) AS vwap, count(*) AS n
        FROM '{D}/trades.parquet' t JOIN '{D}/tokens.parquet' k USING (token_id) WHERE t.walk = 'taker'
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""):
        series.setdefault(c, {}).setdefault(str(i), []).append([h, round(vw, 6), n])
    for c, s in series.items():
        price_files[c] = _write(out / "prices" / f"{c}.json", {"condition_id": c, "source": "derived from API taker-walk fills",
                                                             "resolution": "1 h VWAP", "series": s})
    # wallet pages
    tok = dict(q(con, f"SELECT token_id, outcome_index FROM '{D}/tokens.parquet'"))
    scores = {}
    for sc, w, tp, nna, pp in q(con, f"""SELECT s.scope, s.proxy_wallet, s.total_pipeline, s.n_signals_na, u.passed_prefilter
            FROM '{R}/scores.parquet' s JOIN '{R}/universe.parquet' u USING (scope, proxy_wallet) JOIN lw USING (proxy_wallet)"""):
        scores.setdefault(w, {})[sc] = {"score": tp, "n_signals_na": nna, "prefilter_pass": bool(pp), "signals": {}}
    for sc, w, sid, raw, comp, na, ev in q(con, f"""SELECT g.scope, g.proxy_wallet, g.signal_id, g.raw_value, g.component, g.na_reason, g.evidence_json
            FROM '{R}/signals.parquet' g JOIN lw USING (proxy_wallet)"""):
        e = json.loads(ev or "{}")
        scores[w][sc]["signals"][sid] = {"raw": raw, "component": comp, "na_reason": na, "evidence": e,
                                         **({"incomplete_path": s6_incomplete(raw, e)} if sid == "S6" else {})}
    # S5 mean entry price per (scope, wallet): same bet set as S5 (walk all, before chain resolution, exact net > 0, non-void)
    s5 = q(con, f"""WITH sm AS (SELECT 'all' AS scope, condition_id FROM '{D}/markets.parquet'
                     UNION ALL SELECT condition_id, condition_id FROM '{D}/markets.parquet'
                     UNION ALL SELECT 'event:' || event_id, condition_id FROM '{D}/markets.parquet' WHERE event_id IS NOT NULL),
             b AS (SELECT t.proxy_wallet, t.condition_id, t.token_id,
                          sum(CASE WHEN t.side = 'BUY' THEN CAST(t.size AS DECIMAL(38,6)) ELSE -CAST(t.size AS DECIMAL(38,6)) END) AS net,
                          CAST(CAST(sum(CASE WHEN t.side = 'BUY' THEN CAST(t.size AS DECIMAL(38,6)) * CAST(t.price AS DECIMAL(38,10)) END) AS VARCHAR) AS DOUBLE)
                          / CAST(CAST(sum(CASE WHEN t.side = 'BUY' THEN CAST(t.size AS DECIMAL(38,6)) END) AS VARCHAR) AS DOUBLE) AS entry
                   FROM '{D}/trades.parquet' t JOIN lw USING (proxy_wallet) JOIN '{S4}' r ON r.condition_id = t.condition_id
                   WHERE t.walk = 'all' AND epoch(t.ts) < r.resolution_ts_unix GROUP BY 1, 2, 3)
        SELECT sm.scope, b.proxy_wallet, avg(b.entry), count(*) FROM b JOIN sm USING (condition_id) JOIN '{S4}' r ON r.condition_id = b.condition_id
        WHERE b.net > 0 AND b.entry IS NOT NULL AND NOT r.void GROUP BY 1, 2""")
    for sc, w, me, n in s5:
        if sc in scores.get(w, {}):
            scores[w][sc]["s5_mean_entry"] = me
    wallet_files = {}
    rank_of = {w: i for i, w in enumerate(wallets, 1)}     # wallets arrive in leaderboard order (score desc, address)

    def flush(w, rows):
        wallet_files[w] = _write(out / "wallets" / f"{w}.json", {
            "wallet": w, "rank_all": rank_of.get(w), "n_ranked_all": len(wallets), "scopes": scores.get(w, {}),
            "fills_columns": ["ts_unix", "condition_id", "outcome_index", "is_buy", "size_shares", "price_usdc", "post_resolution"],
            "fills": rows, "fills_note": "walk all (taker + maker legs); API rows, not deduplicated"})
    cur = con.execute(guard(f"""SELECT t.proxy_wallet, epoch(t.ts)::BIGINT, t.condition_id, t.token_id, t.side, t.size, t.price,
            r.resolution_ts_unix FROM '{D}/trades.parquet' t JOIN lw USING (proxy_wallet) JOIN '{S4}' r ON r.condition_id = t.condition_id
            WHERE t.walk = 'all' ORDER BY 1, 2, 3, t.seq"""))
    cur_w, rows = None, []
    while True:
        chunk = cur.fetchmany(100_000)
        if not chunk:
            break
        for w, ts, c, t, side, size, price, rts in chunk:
            if w != cur_w:
                if cur_w is not None:
                    flush(cur_w, rows)
                cur_w, rows = w, []
            rows.append([ts, c, tok.get(t), 1 if side == "BUY" else 0, size, price, 1 if ts >= rts else 0])
    if cur_w is not None:
        flush(cur_w, rows)
    for w in wallets:                      # a ranked wallet always has fills; keep the page consistent regardless
        if w not in wallet_files:
            flush(w, [])
    return {"markets": markets, "wallet_files": wallet_files, "price_files": price_files}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir"); ap.add_argument("snap_dir"); ap.add_argument("out_dir")
    ap.add_argument("--profile-label", default="none (this run used no profile)")
    ap.add_argument("--banner", required=True, help="interim banner text for this run (QA-ruled wording)")
    ap.add_argument("--run-label", required=True, help="short label for the run selector")
    ap.add_argument("--profile-unverified", action="store_true", help="profile writer unverified (PROF-001): mark every S6 > 0 incomplete")
    a = ap.parse_args(argv)
    m = export(Path(a.run_dir), Path(a.snap_dir), Path(a.out_dir), a.profile_label, a.banner, a.run_label, a.profile_unverified)
    print(json.dumps(m, indent=1))


if __name__ == "__main__":
    sys.exit(main())
