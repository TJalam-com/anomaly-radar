"""Ingest step 4 (design note r3 Δ6): chain resolution side-car for an existing SNAP.

Per condition in <SNAP>/derived/markets.parquet:
  one eth_getLogs on CTF 0x4d97…6045, topic0 = ConditionResolution, topic1 = conditionId, blocks FROM_BLOCK..latest
  (tenderly serves the full range for this selective filter), plus eth_getBlockByNumber for the resolution block.
Gates (abort the condition, write no row):  S4-ONE  exactly one ConditionResolution log.
Recorded, not aborting:  U-PAYOUT  chain payouts vs Gamma outcome (markets.resolved_outcome_index)
                         X-G1      tx / block / ts vs results/G1_A21_P2 csv (cross-check file, other script, other day)
Writes <SNAP>/step4/{raw/rpc/*.json, resolutions_chain.parquet, manifest.json}. Never rewrites SNAP files.
t_ref = max(ts) of taker-walk fills with ts < resolution_ts (design note r3 Δ1).

Usage: python -m radar.step4 --snap data/snapshots/SNAP-003 --crosscheck ../results/G1_A21_P2_2026-09-26.csv
"""
import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from Crypto.Hash import keccak

import duckdb

from radar.ingest import _sha256_file
from radar.snapshots import utcnow

RPC = "https://polygon.gateway.tenderly.co"
CTF = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
FROM_BLOCK = 75_000_000  # ~2025-08; every M market was created later (G1: earliest closedTime 2026-02-01)


def _k(s):
    h = keccak.new(digest_bits=256)
    h.update(s.encode())
    return "0x" + h.hexdigest()


CR = _k("ConditionResolution(bytes32,address,bytes32,uint256,uint256[])")
assert _k("hello") == "0x1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"


class Step4Error(RuntimeError):
    pass


class Rpc:
    def __init__(self, url=RPC, transport=None):
        self.url = url
        self.cli = httpx.Client(transport=transport, timeout=120,
                                headers={"User-Agent": "anomaly-radar/0.1 (read-only research)"})
        self.calls = 0

    def call(self, method, params) -> tuple[object, bytes]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(6):
            r = self.cli.post(self.url, json=body)
            self.calls += 1
            if r.status_code == 429 or r.status_code >= 500:
                import time
                time.sleep(2 ** attempt)
                continue
            j = r.json()
            if "error" in j:
                raise Step4Error(f"{method}: {j['error']}")
            return j["result"], r.content
        raise Step4Error(f"{method}: retries exhausted")


def _save(dirpath: Path, name: str, raw: bytes, files: list):
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / name
    p.write_bytes(raw)
    files.append({"path": p.as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})


def resolve_condition(rpc: Rpc, cond: str, raw_dir: Path, files: list) -> dict:
    logs, raw = rpc.call("eth_getLogs", [{"address": CTF, "fromBlock": hex(FROM_BLOCK), "toBlock": "latest",
                                          "topics": [CR, cond]}])
    _save(raw_dir, f"{cond}_logs.json", raw, files)
    if len(logs) != 1:
        raise Step4Error(f"S4-ONE: {len(logs)} ConditionResolution logs for {cond}")
    lg = logs[0]
    blk, rawb = rpc.call("eth_getBlockByNumber", [lg["blockNumber"], False])
    _save(raw_dir, f"{cond}_block.json", rawb, files)
    d = lg["data"][2:]
    words = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
    n_slots = words[0]
    payouts = words[3:3 + n_slots]
    void = len(set(payouts)) == 1
    return {"condition_id": cond, "resolution_tx": lg["transactionHash"], "resolution_log_index": int(lg["logIndex"], 16),
            "resolution_block": int(lg["blockNumber"], 16), "resolution_ts_unix": int(blk["timestamp"], 16),
            "oracle": "0x" + lg["topics"][2][-40:], "payouts_json": json.dumps(payouts), "void": void,
            "chain_winner_index": None if void else payouts.index(max(payouts))}


def run_step4(snap_dir: Path, crosscheck: Path | None = None, rpc: Rpc | None = None) -> dict:
    snap_dir = Path(snap_dir)
    out_dir = snap_dir / "step4"
    if (out_dir / "manifest.json").exists():
        raise Step4Error(f"{out_dir} already finalised; step 4 never overwrites (make a new SNAP or remove by hand)")
    rpc = rpc or Rpc()
    snap_manifest_sha = _sha256_file(snap_dir / "manifest.json")
    con = duckdb.connect()  # in-memory; reads SNAP parquet, writes nothing into the SNAP's own files
    D = (snap_dir / "derived").as_posix()
    markets = con.execute(f"SELECT condition_id, epoch(closed_at)::BIGINT, resolved_outcome_index "
                          f"FROM '{D}/markets.parquet' ORDER BY 1").fetchall()
    xg1 = {}
    if crosscheck:
        raw = Path(crosscheck).read_bytes()
        for r in csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")):
            xg1[r["condition_id"].lower()] = r
        xg1_meta = {"path": str(crosscheck), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    files, rows, results = [], [], {}
    for i, (cond, closed_unix, gamma_idx) in enumerate(markets):
        print(f"[{i + 1}/{len(markets)}] {cond}", file=sys.stderr, flush=True)
        try:
            rec = resolve_condition(rpc, cond, out_dir / "raw" / "rpc", files)
        except Step4Error as e:
            results[cond] = {"status": "aborted", "reason": str(e)}
            continue
        rts = rec["resolution_ts_unix"]
        tref = con.execute(f"SELECT max(epoch(ts))::BIGINT FROM '{D}/trades.parquet' "
                           f"WHERE condition_id = ? AND walk = 'taker' AND epoch(ts) < ?", [cond, rts]).fetchone()[0]
        rec["t_ref_unix"] = tref
        rec["closed_at_delta_s"] = None if closed_unix is None else rts - closed_unix
        checks = {"U-PAYOUT": {"passed": (rec["void"] and gamma_idx is None) or (rec["chain_winner_index"] == gamma_idx),
                               "chain_winner_index": rec["chain_winner_index"], "void": rec["void"], "gamma_index": gamma_idx}}
        g = xg1.get(cond)
        if crosscheck:
            same = bool(g) and g["resolution_tx"].lower() == rec["resolution_tx"].lower() \
                and int(g["resolution_block"]) == rec["resolution_block"] and int(g["resolution_ts"]) == rts
            checks["X-G1"] = {"passed": same, "g1_present": bool(g)}
        results[cond] = {"status": "resolved", "checks": checks}
        rows.append(rec)
    cols = ["condition_id", "resolution_tx", "resolution_log_index", "resolution_block", "resolution_ts_unix",
            "oracle", "payouts_json", "void", "chain_winner_index", "t_ref_unix", "closed_at_delta_s"]
    tbl = pa.table({c: [r[c] for r in rows] for c in cols},
                   schema=pa.schema([("condition_id", pa.string()), ("resolution_tx", pa.string()),
                                     ("resolution_log_index", pa.int64()), ("resolution_block", pa.int64()),
                                     ("resolution_ts_unix", pa.int64()), ("oracle", pa.string()),
                                     ("payouts_json", pa.string()), ("void", pa.bool_()),
                                     ("chain_winner_index", pa.int64()), ("t_ref_unix", pa.int64()),
                                     ("closed_at_delta_s", pa.int64())]))
    out_dir.mkdir(parents=True, exist_ok=True)
    pp = out_dir / "resolutions_chain.parquet"
    pq.write_table(tbl, pp)
    status = "ok" if rows and len(rows) == len(markets) else "incomplete"
    body = json.dumps({"step": 4, "snap": snap_dir.name, "snap_manifest_sha256": snap_manifest_sha,
                       "finished_at": utcnow(), "status": status, "rpc": RPC, "rpc_calls": rpc.calls,
                       "from_block": FROM_BLOCK, "crosscheck": xg1_meta if crosscheck else None,
                       "files": [{**f, "path": Path(f["path"]).relative_to(snap_dir).as_posix()} for f in files],
                       "derived": [{"file": pp.relative_to(snap_dir).as_posix(), "rows": len(rows),
                                    "sha256": _sha256_file(pp)}],
                       "results": results}, indent=1, sort_keys=True).encode()
    (out_dir / "manifest.json").write_bytes(body)
    return {"status": status, "rows": len(rows), "markets": len(markets), "rpc_calls": rpc.calls,
            "manifest": str(out_dir / "manifest.json"), "manifest_sha256": hashlib.sha256(body).hexdigest(),
            "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True)
    ap.add_argument("--crosscheck")
    a = ap.parse_args(argv)
    out = run_step4(Path(a.snap), Path(a.crosscheck) if a.crosscheck else None)
    summary = {k: v for k, v in out.items() if k != "results"}
    fails = {c: {k: v for k, v in r.get("checks", {}).items() if not v["passed"]}
             for c, r in out["results"].items() if r["status"] != "resolved" or any(not v["passed"] for v in r.get("checks", {}).values())}
    print(json.dumps({**summary, "non_passing": fails}, indent=1))
    return 0 if out["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
