"""Phase-1 ingest, design note §3 steps 1-3, one condition at a time.

Step 1  Gamma market (+ tokens)
Step 2  v2/trades, two walks: 'taker' (default taker_only=true) and 'all' (taker_only=false)
Step 3  v2/holders, v2/positions, v2/resolutions

Control gates (abort the market, load nothing):
  G-MARKET  Gamma lookup must return exactly one market for the condition
  G-TRADES  taker walk returned 0 rows while Gamma volume > 0          (design note §3.2)
  G-A2B     'all' walk row count == 'taker' walk row count               (misspelt/ignored taker_only; census F4)
Recorded, not aborting:
  U-UNITS   Σ taker-walk size vs Gamma volume (both shares; census r2 M1)

Usage:  python -m radar.ingest --condition 0x...
        python -m radar.ingest --m-csv ../results/G0_M_phase1_markets_2026-09-26.csv   (include==True rows)
"""
import argparse
import csv
import io
import hashlib
import json
import sys
from datetime import datetime, timezone

import duckdb
import pyarrow as pa

from radar import config, schema
from radar.http import Fetcher
from radar.snapshots import SnapshotRun, utcnow


class IngestError(RuntimeError):
    pass


class ControlGateError(IngestError):
    pass


def _ts_unix(v):
    return datetime.fromtimestamp(int(v), tz=timezone.utc) if v not in (None, "") else None


def _ts_iso(v):
    if not v:
        return None
    s = v.replace("Z", "+00:00")
    if s.endswith("+00") and not s.endswith("+00:00"):
        s += ":00"
    return datetime.fromisoformat(s.replace(" ", "T"))


def walk(fetcher: Fetcher, run: SnapshotRun, source: str, name: str, url: str, params: dict):
    """Cursor walk until next_cursor is null. Returns [(snapshot_id, seq, row), ...]."""
    out, cursor, page = [], None, 0
    while True:
        p = dict(params, limit=config.PAGE_LIMIT)
        if cursor:
            p["cursor"] = cursor
        status, body, full_url = fetcher.get(url, p)
        rec = run.save(source, name, page, full_url, p, status, body)
        if status != 200:
            raise IngestError(f"{name} page {page}: HTTP {status}: {body[:200]!r}")
        j = json.loads(body)
        for seq, row in enumerate(j.get("data", [])):
            out.append((rec["snapshot_id"], seq, row))
        page += 1
        cursor = (j.get("pagination") or {}).get("next_cursor")
        if not cursor:
            return out


def check_trades_gate(gamma_volume: float, n_taker: int, n_all: int) -> None:
    if n_taker == 0 and gamma_volume > 0:
        raise ControlGateError(f"G-TRADES: taker walk returned 0 rows but Gamma volume = {gamma_volume}")
    if n_all == n_taker:
        raise ControlGateError(f"G-A2B: taker_only=false walk rows ({n_all}) == taker walk rows ({n_taker}); "
                               "parameter likely ignored")


def fetch_market(cond: str, fetcher: Fetcher, run: SnapshotRun) -> dict:
    """Fetch + gate one condition. Returns parsed rows; loads nothing."""
    url = f"{config.GAMMA}/markets"
    params = {"condition_ids": cond, "closed": "true"}  # closed markets are silently omitted without closed=true
    status, body, full_url = fetcher.get(url, params)
    grec = run.save("gamma", f"markets_{cond}", 0, full_url, params, status, body)
    if status != 200:
        raise IngestError(f"gamma HTTP {status}")
    ms = [m for m in json.loads(body) if m.get("conditionId", "").lower() == cond.lower()]
    if len(ms) != 1:
        raise ControlGateError(f"G-MARKET: Gamma returned {len(ms)} markets for {cond}")
    m = ms[0]
    gamma_volume = float(m.get("volume") or 0)

    base = {"condition": cond}
    taker = walk(fetcher, run, "dataapi_v2", f"trades_taker_{cond}", f"{config.DATA_API}/v2/trades", base)
    allw = walk(fetcher, run, "dataapi_v2", f"trades_all_{cond}", f"{config.DATA_API}/v2/trades",
                dict(base, taker_only="false"))
    check_trades_gate(gamma_volume, len(taker), len(allw))

    holders = walk(fetcher, run, "dataapi_v2", f"holders_{cond}", f"{config.DATA_API}/v2/holders", base)
    positions = walk(fetcher, run, "dataapi_v2", f"positions_{cond}", f"{config.DATA_API}/v2/positions", base)
    resolutions = walk(fetcher, run, "dataapi_v2", f"resolutions_{cond}", f"{config.DATA_API}/v2/resolutions", base)
    return {"cond": cond, "gamma": m, "gamma_snapshot_id": grec["snapshot_id"], "gamma_volume": gamma_volume,
            "taker": taker, "all": allw, "holders": holders, "positions": positions, "resolutions": resolutions}


def _bulk(con, table: str, rows: list[list]) -> None:
    """Insert rows positionally via an Arrow table (DuckDB executemany is row-by-row and took >10 min for 300k rows)."""
    if not rows:
        return
    cols = [c for (c,) in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [table]).fetchall()]
    if len(rows[0]) != len(cols):
        raise IngestError(f"{table}: row width {len(rows[0])} != {len(cols)} columns")
    tbl = pa.table({c: [r[i] for r in rows] for i, c in enumerate(cols)})
    con.register("_bulk_src", tbl)
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM _bulk_src")
    finally:
        con.unregister("_bulk_src")


def load_market(con, run: SnapshotRun, f: dict) -> dict:
    """Insert one gated market into DuckDB inside one transaction. Returns check results."""
    cond, m = f["cond"], f["gamma"]
    toks = json.loads(m.get("clobTokenIds") or "[]")
    outs = json.loads(m.get("outcomes") or "[]")
    prices = json.loads(m.get("outcomePrices") or "[]")
    resolved = next((i for i, p in enumerate(prices) if float(p) >= 0.99), None)
    ev = (m.get("events") or [{}])[0].get("id")

    sum_taker_size = sum(r["size"] for _, _, r in f["taker"])
    ratio = sum_taker_size / f["gamma_volume"] if f["gamma_volume"] else None
    checks = {
        "G-MARKET": (True, {"markets": 1}),
        "G-TRADES": (True, {"taker_rows": len(f["taker"]), "gamma_volume_shares": f["gamma_volume"]}),
        "G-A2B": (True, {"taker_rows": len(f["taker"]), "all_rows": len(f["all"])}),
        "U-UNITS": (ratio is not None and abs(ratio - 1) <= 0.01,
                    {"sum_taker_size_shares": sum_taker_size, "gamma_volume_shares": f["gamma_volume"],
                     "ratio": ratio, "tolerance": 0.01, "walk": "taker"}),
    }

    con.execute("BEGIN")
    try:
        _bulk(con, "snapshots", [[r["snapshot_id"], r["snap"], r["source"], r["name"], r["page"], r["endpoint"],
                          r["params_json"], r["fetched_at"], r["http_status"], None, r["bytes"], r["path"]]
                         for r in run.records if r["name"].endswith(cond)])
        con.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            cond, m.get("id"), ev, m.get("question"), bool(m.get("negRisk")),
            _ts_iso(m.get("createdAt")), _ts_iso(m.get("acceptingOrdersTimestamp")), _ts_iso(m.get("closedTime")),
            resolved, None, None,  # resolution_tx/ts come from chain ConditionResolution (A21), not v2/resolutions
            f["gamma_volume"], f["gamma_snapshot_id"]])
        con.executemany("INSERT OR REPLACE INTO tokens VALUES (?,?,?,?)",
                        [[str(t), cond, outs[i] if i < len(outs) else None, i] for i, t in enumerate(toks)])
        for walk_label in ("taker", "all"):
            _bulk(con, "trades", [
                [sid, seq, walk_label, r["transaction_hash"], None, r["condition_id"], r["token_id"],
                 r["proxy_wallet"], r["side"], r["size"], r["price"], _ts_unix(r["timestamp"]), "dataapi_v2"]
                for sid, seq, r in f[walk_label]])
        hrows, hseq = [], {}
        for sid, _, tok in f["holders"]:
            for h in tok["holders"]:
                hseq[sid] = hseq.get(sid, -1) + 1
                hrows.append([sid, hseq[sid], h["token_id"], h["proxy_wallet"], h["amount"], h.get("outcome_index")])
        _bulk(con, "holders_snap", hrows)
        _bulk(con, "positions_snap", [
            [sid, seq, r["proxy_wallet"], r["token_id"], r["condition_id"], r.get("current_size"),
             r.get("total_size"), r.get("avg_price"), r.get("total_cost_usdc"), r.get("realized_pnl"),
             r.get("total_pnl"), r.get("status")] for sid, seq, r in f["positions"]])
        _bulk(con, "api_resolutions", [
            [sid, r["condition_id"], r.get("question_id"), r.get("status"), r.get("was_disputed"), r.get("price"),
             r.get("transaction_hash"), _ts_unix(r.get("last_update_timestamp"))] for sid, _, r in f["resolutions"]])
        con.executemany("INSERT OR REPLACE INTO ingest_checks VALUES (?,?,?,?,?)",
                        [[run.snap, cond, k, v[0], json.dumps(v[1])] for k, v in checks.items()])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {k: {"passed": v[0], **v[1]} for k, v in checks.items()}


def connect(db_path):
    """DuckDB with a hard memory cap and spill directory on D: (principal D-007; SNAP-003 export OOM'd at default)."""
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(f"SET memory_limit = '{config.DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET temp_directory = '{config.TMP_DIR.as_posix()}'")
    con.execute("SET preserve_insertion_order = false")
    return con


def _sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


EXPORT_TABLES = ["trades", "holders_snap", "positions_snap", "api_resolutions", "markets", "tokens",
                 "snapshots", "ingest_checks", "ingest_results"]


def export_parquet(con, snap_dir) -> list[dict]:
    """Whole-table parquet per derived table. The DB holds exactly one SNAP, so no filtering join is needed."""
    d = snap_dir / "derived"
    d.mkdir(exist_ok=True)
    out = []
    for table in EXPORT_TABLES:
        p = d / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{p.as_posix()}' (FORMAT PARQUET)")
        rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        out.append({"file": p.relative_to(snap_dir).as_posix(), "rows": rows, "sha256": _sha256_file(p)})
    return out


def finalize(con, snap_dir, inputs: dict, started_at: str | None = None) -> dict:
    """Export parquet and write manifest.json from the DB (one path for fresh and resumed runs).
    Manifest file list = snapshots table; raw files on disk are checked against it in both directions."""
    derived = export_parquet(con, snap_dir)
    keys = ["snapshot_id", "snap", "source", "name", "page", "endpoint", "params_json", "fetched_at",
            "http_status", "bytes", "path"]
    files = [dict(zip(keys, r)) for r in con.execute(
        "SELECT snapshot_id, snap, source, request_name, page, endpoint, params_json, "
        "strftime(fetched_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ'), http_status, bytes, path "
        "FROM snapshots ORDER BY path").fetchall()]
    listed = {f["path"] for f in files}
    on_disk = {q.relative_to(snap_dir).as_posix() for q in (snap_dir / "raw").rglob("*.json")}
    results = {}
    for cond, st, reason in con.execute("SELECT condition_id, status, reason FROM ingest_results ORDER BY 1").fetchall():
        results[cond] = {"status": st, **({"reason": reason} if reason else {})}
    for cond, cid, passed, detail in con.execute(
            "SELECT condition_id, check_id, passed, detail_json FROM ingest_checks ORDER BY 1, 2").fetchall():
        results.setdefault(cond, {"status": "loaded"}).setdefault("checks", {})[cid] = {"passed": passed, **json.loads(detail)}
    status = "ok" if results and all(r["status"] == "loaded" for r in results.values()) else "aborted"
    if on_disk != listed:
        status = "provenance_mismatch"
    body = json.dumps({"snap": snap_dir.name, "started_at": started_at, "finished_at": utcnow(), "status": status,
                       "files": files, "raw_on_disk_not_listed": sorted(on_disk - listed),
                       "listed_not_on_disk": sorted(listed - on_disk), "results": results,
                       "derived": derived, "inputs": inputs}, indent=1, sort_keys=True, default=str).encode()
    mp = snap_dir / "manifest.json"
    mp.write_bytes(body)
    return {"snap": snap_dir.name, "status": status, "results": results, "manifest": str(mp),
            "db": str(snap_dir / "radar.duckdb"), "manifest_sha256": hashlib.sha256(body).hexdigest(),
            "derived": derived}


def _record_fetches(con, run: SnapshotRun, cond: str) -> None:
    """Provenance rows for an aborted market (a loaded market inserts its own inside load_market)."""
    rows = [[r["snapshot_id"], r["snap"], r["source"], r["name"], r["page"], r["endpoint"], r["params_json"],
             r["fetched_at"], r["http_status"], None, r["bytes"], r["path"]]
            for r in run.records if r["name"].endswith(cond)]
    _bulk(con, "snapshots", rows)


def run_ingest(conditions: list[str], fetcher: Fetcher | None = None, snap_root=None, inputs: dict | None = None) -> dict:
    fetcher = fetcher or Fetcher()
    run = SnapshotRun(**({"root": snap_root} if snap_root else {}))
    con = connect(run.dir / "radar.duckdb")  # derived DB per SNAP: rebuilt, never migrated or merged across SNAPs
    schema.create(con)
    conds = list(dict.fromkeys(c.lower() for c in conditions))  # a repeated condition would refetch identical pages
    for i, cond in enumerate(conds):
        print(f"[{i + 1}/{len(conds)}] {cond}", file=sys.stderr, flush=True)
        try:
            f = fetch_market(cond, fetcher, run)
            load_market(con, run, f)
            con.execute("INSERT INTO ingest_results VALUES (?, 'loaded', NULL)", [cond])
        except ControlGateError as e:
            _record_fetches(con, run, cond)
            con.execute("INSERT INTO ingest_results VALUES (?, 'aborted', ?)", [cond, str(e)])
    out = finalize(con, run.dir, inputs or {}, run.started_at)
    con.close()
    return out


def resume_finalize(snap_dir, inputs: dict) -> dict:
    """Finish a SNAP whose fetch+load completed but whose export/manifest did not (SNAP-003: export OOM).
    Backfills ingest_results from ingest_checks only when that table is empty (runs predating it)."""
    con = connect(snap_dir / "radar.duckdb")
    schema.create(con)
    if con.execute("SELECT count(*) FROM ingest_results").fetchone()[0] == 0:
        con.execute("INSERT INTO ingest_results SELECT DISTINCT condition_id, 'loaded', "
                    "'backfilled from ingest_checks at finalize' FROM ingest_checks")
    out = finalize(con, snap_dir, dict(inputs, resumed_finalize_at=utcnow()))
    con.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", action="append", default=[])
    ap.add_argument("--m-csv", help="market-set CSV; rows with include==True are ingested")
    ap.add_argument("--resume-finalize", metavar="SNAP_DIR", help="export + manifest for a SNAP whose load finished")
    a = ap.parse_args(argv)
    conds, inputs = list(a.condition), {}
    if a.m_csv:
        raw = open(a.m_csv, "rb").read()
        rows = [r for r in csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")) if r["include"] == "True"]
        conds += [r["condition_id"] for r in rows]
        inputs["m_csv"] = {"path": a.m_csv, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                           "include_rows": len(rows)}
    if a.resume_finalize:
        from pathlib import Path
        out = resume_finalize(Path(a.resume_finalize), inputs)
        print(json.dumps(out, indent=1, default=str))
        return 0 if out["status"] == "ok" else 2
    if not conds:
        ap.error("give --condition and/or --m-csv")
    out = run_ingest(conds, inputs=inputs)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
