"""Independent recount of one SNAP. Does NOT import radar.* - reads raw bytes and derived parquet directly.

Checks:
  1. every manifest file re-hashes to its snapshot_id; no raw file on disk missing from the manifest (both directions)
  2. per condition, rows counted from raw JSON == rows in derived parquet, per request kind
     (trades_taker / trades_all / holders / positions / resolutions)
  3. per condition, Σ taker size from raw == Σ size of walk='taker' rows in parquet
Usage: python tools/recount.py data/snapshots/SNAP-003
"""
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

snap = Path(sys.argv[1])
man = json.loads((snap / "manifest.json").read_bytes())
listed = {f["path"] for f in man["files"]}
on_disk = {p.relative_to(snap).as_posix() for p in (snap / "raw").rglob("*.json")}
bad_hash = [f["path"] for f in man["files"] if hashlib.sha256((snap / f["path"]).read_bytes()).hexdigest() != f["snapshot_id"]]
print(f"manifest files {len(listed)}  on disk {len(on_disk)}  unlisted-on-disk {len(on_disk - listed)}  "
      f"listed-missing {len(listed - on_disk)}  hash mismatches {len(bad_hash)}")

loaded = {c for c, r in man["results"].items() if r["status"] == "loaded"}
raw_n = defaultdict(int)
raw_taker_size = defaultdict(float)
for f in man["files"]:
    kind, _, cond = f["name"].rpartition("_")
    if cond not in loaded or f["http_status"] != 200:
        continue
    d = json.loads((snap / f["path"]).read_bytes())
    if kind.startswith("markets"):
        continue
    if kind == "holders":
        n = sum(len(t["holders"]) for t in d["data"])
    else:
        n = len(d["data"])
    raw_n[(cond, kind)] += n
    if kind == "trades_taker":
        raw_taker_size[cond] += sum(r["size"] for r in d["data"])

con = duckdb.connect()
dv = snap / "derived"
pq = defaultdict(int)
for (cond, walk, n) in con.execute(f"SELECT condition_id, walk, count(*) FROM '{(dv/'trades.parquet').as_posix()}' GROUP BY 1,2").fetchall():
    pq[(cond, "trades_" + walk)] = n
for (cond, n) in con.execute(f"SELECT condition_id, count(*) FROM '{(dv/'positions_snap.parquet').as_posix()}' GROUP BY 1").fetchall():
    pq[(cond, "positions")] = n
for (cond, n) in con.execute(f"SELECT condition_id, count(*) FROM '{(dv/'api_resolutions.parquet').as_posix()}' GROUP BY 1").fetchall():
    pq[(cond, "resolutions")] = n
for (cond, n) in con.execute(
        f"SELECT k.condition_id, count(*) FROM '{(dv/'holders_snap.parquet').as_posix()}' h "
        f"JOIN '{(dv/'tokens.parquet').as_posix()}' k USING (token_id) GROUP BY 1").fetchall():
    pq[(cond, "holders")] = n
pq_size = dict(con.execute(f"SELECT condition_id, sum(size) FROM '{(dv/'trades.parquet').as_posix()}' WHERE walk='taker' GROUP BY 1").fetchall())

keys = set(raw_n) | set(pq)
mism = [(k, raw_n.get(k, 0), pq.get(k, 0)) for k in sorted(keys) if raw_n.get(k, 0) != pq.get(k, 0)]
size_mism = [(c, raw_taker_size[c], pq_size.get(c)) for c in loaded if abs(raw_taker_size[c] - (pq_size.get(c) or 0)) > 1e-9 * max(1.0, raw_taker_size[c])]
tot = defaultdict(int)
for (c, kind), n in raw_n.items():
    tot[kind] += n
print(f"loaded conditions {len(loaded)}  aborted {len(man['results']) - len(loaded)}")
print("raw totals", dict(tot))
print(f"count mismatches raw vs parquet: {len(mism)}  {mism[:10]}")
print(f"taker size mismatches: {len(size_mism)}  {size_mism[:5]}")
sys.exit(1 if (bad_hash or mism or size_mism or (on_disk - listed) or (listed - on_disk)) else 0)
