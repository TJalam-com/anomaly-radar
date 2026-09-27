"""Derive G BLIND from a profile's activity lookups, BEFORE any scoring (params v3 s6_g_*; D1 r16b §3 procedure steps 2-3).
Population: complete lookup records of counterparties shared by >= 2 NATURAL wallets (natural = passed_prefilter on the SNAP views),
with edges and activity as of block(SNAP) = the profile's snap_block. Natural sharers are recomputed here from transfers at snap_block
(fetch-time counts in the lookup files are not trusted). Writes the G record JSON; QA records its sha256 in ledger/G_RECORDS.md;
Tester 1 recomputes independently. Reads no score, no signal, no control data.
usage: python tools/derive_g.py --snap <SNAP> --prof <PROF-dir> --params config/params_frozen_2026-09-27_v3.toml --out <g_record.json>"""
import argparse
import hashlib
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar import config, entities, knee_g, scoring_view, signals  # noqa: E402


def natural_wallets(snap: Path, P: dict) -> set:
    con = duckdb.connect()
    con.execute("SET threads=1"); con.execute("SET memory_limit='1GB'")
    con.execute(f"SET temp_directory='{config.TMP_DIR.as_posix()}'")
    scoring_view.build_scoring_views(con, snap)
    D = (snap / "derived").as_posix()
    con.execute(f"CREATE VIEW trades_all_any AS SELECT * FROM '{D}/trades.parquet' WHERE walk = 'all'")
    con.execute(f"CREATE VIEW tokens_v AS SELECT * FROM '{D}/tokens.parquet'")
    con.execute("CREATE TABLE scope_map AS SELECT 'all' AS scope, condition_id FROM markets_r")
    con.execute("CREATE TABLE bypass(proxy_wallet TEXT)")          # natural = passed_prefilter: the bypass list is irrelevant here
    signals.universe(con, P)
    return {r[0] for r in con.execute("SELECT proxy_wallet FROM universe_t WHERE scope = 'all' AND passed_prefilter").fetchall()}


def derive(snap: Path, prof: Path, params: Path) -> dict:
    praw = params.read_bytes()
    P = tomllib.loads(praw.decode("utf-8"))["params"]
    if P.get("s6_rule") != "C1":
        raise SystemExit("derive_g is for params v3 (s6_rule = C1)")
    man = json.loads((prof / "manifest.json").read_bytes())
    bt = int(man["snap_block"])
    natural = natural_wallets(snap, P)
    PD = (prof / "derived").as_posix()
    con = duckdb.connect(); con.execute("SET threads=1")
    con.execute("CREATE TABLE stop AS SELECT unnest(?::VARCHAR[]) AS a", [sorted({r[0].lower() for r in entities.rows()})])
    nat_share = {}
    for x, w in con.execute(f"""SELECT DISTINCT lower(counterparty), proxy_wallet FROM '{PD}/transfers.parquet'
            WHERE hop = 1 AND direction IN ('in', 'out') AND first_block IS NOT NULL AND first_block <= {bt}
              AND lower(counterparty) NOT IN (SELECT a FROM stop)""").fetchall():
        if w in natural and x != w:
            nat_share[x] = nat_share.get(x, 0) + 1
    recs = {}
    for a, status, capped, cap_block, from_block, to_block, subs in con.execute(
            f"SELECT address, status, capped, cap_block, from_block, to_block, subranges_json FROM '{PD}/lookups.parquet'").fetchall():
        recs[a] = {"status": status, "capped": bool(capped), "cap_block": cap_block, "from_block": from_block, "to_block": to_block,
                   "subranges": json.loads(subs or "[]"), "first_blocks": []}
    for a, fb in con.execute(f"SELECT address, first_block FROM '{PD}/lookup_y.parquet'").fetchall():
        recs[a]["first_blocks"].append(fb)
    for a, r in recs.items():
        r["complete"] = knee_g.complete_at(r, bt)
        r["n_natural_sharers"] = nat_share.get(a, 0)
    pop = knee_g.g_population(list(recs.values()), bt)
    res = knee_g.g_from_activities(pop)                              # raises GUndefined: stop, report to QA, no fallback
    derived = {d["file"]: d["sha256"] for d in man["derived"]}
    return {"prof_id": man["prof_id"], "G": res["G"], "mode_bin": res["mode_bin"], "histogram": res["histogram"],
            "population_n": len(pop), "lookup_records": len(recs),
            "records_excluded": {"incomplete": sum(1 for r in recs.values() if not r["complete"]),
                                 "lt2_natural_sharers": sum(1 for r in recs.values() if r["n_natural_sharers"] < 2)},
            "snap_block": bt, "snap_instant_unix": man.get("snap_instant_unix"),
            "snap_manifest_sha256": hashlib.sha256((snap / "manifest.json").read_bytes()).hexdigest(),   # R15V3-3: the SNAP G was derived on
            "inputs": {f: derived[f] for f in ("derived/lookups.parquet", "derived/lookup_y.parquet", "derived/transfers.parquet")},
            "params_sha256": hashlib.sha256(praw).hexdigest(), "formula": P["s6_g_formula"], "population_rule": P["s6_g_population"],
            "knee_g_sha256": hashlib.sha256((ROOT / "radar" / "knee_g.py").read_bytes()).hexdigest(),
            "computed_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True); ap.add_argument("--prof", required=True)
    ap.add_argument("--params", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"{out} exists: G records are never overwritten")
    rec = derive(Path(a.snap), Path(a.prof), Path(a.params))
    body = json.dumps(rec, indent=1, sort_keys=True).encode()
    out.write_bytes(body)
    print(json.dumps({"G": rec["G"], "histogram": rec["histogram"], "mode_bin": rec["mode_bin"], "population_n": rec["population_n"],
                      "g_record": str(out), "sha256": hashlib.sha256(body).hexdigest(),
                      "ledger_line": f"GREC | {rec['prof_id']} | {hashlib.sha256(body).hexdigest()} | G={rec['G']} | <instant> | QA"}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
