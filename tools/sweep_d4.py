"""G-HO-2 route (d'): SAMPLED chain check over every M include market (QA pre-registration 2026-09-26 ~17:4xZ).

Target (pre-registered, not K): pooled UNIFORM-window p95 missing-fill rate <= 1.0% across M, with 0 mismatches in
both directions in every market. p95 = 1 - 0.05**(1/n), n = pooled chain fills seen in uniform windows (needs n >= 299).
Per market: tools/chain_fill_check.py (exchange chosen by neg_risk; NegRisk mapping must be calibrated first).
Resumable: a market with an existing <out>/<condition>.json is skipped. Stops on the first non-zero exit.
Usage: python tools/sweep_d4.py --out ../scratch/developer/d8sweep --k 10 --seed 7 [--conditions-file f]
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import duckdb

SNAP = "data/snapshots/SNAP-003/derived"


def run_one(out, c, args, tag=""):
    f = out / f"{c}{tag}.json"
    if f.exists():
        return 0, f
    r = subprocess.run([sys.executable, "tools/chain_fill_check.py", c, *args, "--out", str(f)], capture_output=True, text=True)
    (out / f"{c}{tag}.log").write_bytes((r.stdout + r.stderr).encode("utf-8"))
    if r.returncode != 0 and f.exists():
        f.unlink()   # never keep a result from a failed run
    return r.returncode, f


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--k-retry-uniform", type=int, default=40)
    ap.add_argument("--w", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--summarise-only", action="store_true")
    ap.add_argument("--snap", default=SNAP, help="derived dir of the snapshot")
    a = ap.parse_args(argv)
    SNAPD = a.snap
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    conds = [c for (c,) in con.execute(f"SELECT condition_id FROM '{SNAPD}/markets.parquet' ORDER BY 1").fetchall()]
    size = dict(con.execute(f"SELECT condition_id, sum(size) FROM '{SNAPD}/trades.parquet' WHERE walk = 'taker' GROUP BY 1").fetchall())
    base = ["--k", str(a.k), "--w", str(a.w), "--seed", str(a.seed)]
    retry = ["--k", str(a.k), "--k-centred", "0", "--k-uniform", str(a.k_retry_uniform), "--w", str(a.w), "--seed", str(a.seed)]
    if not a.summarise_only:
        for i, c in enumerate(conds):                       # pass 1: K centred + K uniform
            rc, f = run_one(out, c, base)
            print(f"[{i + 1}/{len(conds)}] {c} exit {rc}", flush=True)
            if rc != 0:
                sys.exit(f"stopped at {c}: exit {rc}")
        for c in conds:                                     # pass 2 (QA rule 2): 0 uniform fills -> K_retry uniform windows
            j = json.loads((out / f"{c}.json").read_bytes())
            if j["limits"]["chain_fills_in_uniform_windows"] == 0:
                rc, f = run_one(out, c, retry, tag=".retry")
                print(f"retry {c} exit {rc}", flush=True)
                if rc != 0:
                    sys.exit(f"stopped at retry {c}: exit {rc}")
    rows, n_uni, n_all, mism = [], 0, 0, 0
    tool_shas = set()
    for c in conds:
        f = out / f"{c}.json"
        if not f.exists():
            continue
        runs = [json.loads(f.read_bytes())]
        fr = out / f"{c}.retry.json"
        if fr.exists():
            runs.append(json.loads(fr.read_bytes()))
        tool_shas |= {j.get("tool_sha256") for j in runs}
        uni = sum(j["limits"]["chain_fills_in_uniform_windows"] for j in runs)
        allf = sum(j["limits"]["chain_fills_in_all_windows"] for j in runs)
        oc = sum(j["totals"]["on_chain_not_api"] for j in runs)
        ia = sum(j["totals"]["in_api_not_chain"] for j in runs)
        n_uni += uni; n_all += allf; mism += oc + ia
        rows.append({"condition_id": c, "neg_risk": runs[0].get("neg_risk"), "retried": len(runs) > 1,
                     "chain_fills": sum(j["totals"]["chain_fills"] for j in runs), "api_rows": sum(j["totals"]["api_rows"] for j in runs),
                     "on_chain_not_api": oc, "in_api_not_chain": ia, "uniform_fills": uni,
                     "p95_uniform": (1 - 0.05 ** (1 / uni)) if uni else None,
                     "not_exercised": uni == 0, "taker_size_shares": size.get(c, 0.0),
                     "rpc_calls": sum(j["rpc"].get("http_200", 0) for j in runs)})
    p95 = (1 - 0.05 ** (1 / n_uni)) if n_uni else None
    tot_size = sum(size.get(c, 0.0) for c in conds)
    ne = [r for r in rows if r["not_exercised"]]
    ne_share = (sum(r["taker_size_shares"] for r in ne) / tot_size) if tot_size else None
    complete = len(rows) == len(conds)
    rule1 = bool(p95 is not None and p95 <= 0.01 and mism == 0)
    rule3 = bool(ne_share is not None and ne_share < 0.05)
    summary = {"markets_done": len(rows), "markets_total": len(conds), "k": a.k, "k_retry_uniform": a.k_retry_uniform,
               "w": a.w, "seed": a.seed, "pooled_uniform_fills": n_uni, "pooled_p95_uniform": p95,
               "pooled_all_fills": n_all, "pooled_p95_all": (1 - 0.05 ** (1 / n_all)) if n_all else None,
               "mismatches_total": mism, "fills_needed_for_1pct": math.ceil(math.log(0.05) / math.log(0.99)),
               "markets_retried": sum(1 for r in rows if r["retried"]),
               "not_exercised_count": len(ne), "not_exercised_list": [r["condition_id"] for r in ne],
               "not_exercised_taker_size_share": ne_share,
               "uniform_fills_per_market_quartiles": (lambda v: [v[0], v[len(v) // 4], v[len(v) // 2], v[3 * len(v) // 4], v[-1]] if v else None)(
                   sorted(r["uniform_fills"] for r in rows)),
               "rule1_pooled_p95_le_1pct_and_0_mismatch": rule1, "rule3_not_exercised_share_lt_5pct": rule3,
               "tool_sha256_set": sorted(x or "missing" for x in tool_shas),
               "target_met": bool(complete and rule1 and rule3 and len(tool_shas) == 1 and None not in tool_shas)}
    (out / "summary.json").write_bytes(json.dumps({"summary": summary, "rows": rows}, indent=1).encode("utf-8"))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
