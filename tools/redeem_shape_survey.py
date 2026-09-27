"""Q2 measurement: tx-shape distribution of REDEEM activity rows (design note r6 Δ6 D-b).

Picks >= N_MARKETS non-void M markets (SNAP-003 + step4), fixed seed; for each, the top buyers of the winning
token (all walk, signal_fills) are queried for REDEEM rows in that condition until >= N_TOTAL REDEEMs are collected.
Each REDEEM tx is classified by eth_getTransactionByHash:
  safe_exec  : tx.to == proxy_wallet and selector 0x6a761202 (Gnosis Safe execTransaction)
  relay_hub  : tx.to == 0xd216153c…f494 (Polymarket Relay Hub) and selector 0x405cec67 (relayCall)
  other      : anything else (to, selector recorded)
Usage: python tools/redeem_shape_survey.py <out.json>
"""
import json
import random
import sys
import time
from collections import Counter

import duckdb
import httpx
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radar import config  # noqa: E402  (spill dir derived from the app's data dir, not a hard-coded path)

SNAP = "data/snapshots/SNAP-003"
RELAY_HUB = "0xd216153c06e857cd7f72665e0af1d7d82172f494"
N_MARKETS, N_TOTAL, PER_MARKET_WALLETS, SEED = 12, 240, 40, 20260926

con = duckdb.connect()
con.execute("SET memory_limit='400MB'"); con.execute("SET threads=1")
con.execute(f"SET temp_directory='{config.TMP_DIR.as_posix()}'")
mk = con.execute(f"""SELECT m.condition_id, m.neg_risk, r.chain_winner_index, r.resolution_ts_unix
    FROM '{SNAP}/derived/markets.parquet' m JOIN '{SNAP}/step4/resolutions_chain.parquet' r USING (condition_id)
    WHERE NOT r.void ORDER BY 1""").fetchall()
rng = random.Random(SEED)
rng.shuffle(mk)
api = httpx.Client(timeout=60, headers={"User-Agent": "anomaly-radar/0.1 (read-only research)"})
rpc = httpx.Client(timeout=60)
stats = Counter()
rows = []
markets_used = []
for cond, neg, win, rts in mk:
    if len(markets_used) >= N_MARKETS and len(rows) >= N_TOTAL:
        break
    tok = con.execute(f"SELECT token_id FROM '{SNAP}/derived/tokens.parquet' WHERE condition_id=? AND outcome_index=?",
                      [cond, win]).fetchone()[0]
    ws = [w for (w,) in con.execute(f"""SELECT proxy_wallet FROM '{SNAP}/derived/trades.parquet'
        WHERE condition_id=? AND walk='all' AND side='BUY' AND token_id=? AND epoch(ts) < ?
        GROUP BY 1 ORDER BY sum(size) DESC LIMIT {PER_MARKET_WALLETS}""", [cond, tok, rts]).fetchall()]
    got = 0
    for w in ws:
        r = api.get("https://data-api.polymarket.com/v2/activity", params={"user": w, "condition": cond, "type": "REDEEM", "limit": 20})
        stats[f"api_{r.status_code}"] += 1
        if r.status_code != 200:
            continue
        for x in r.json().get("data", []):
            rows.append({"condition_id": cond, "neg_risk": neg, "wallet": w, "tx": x["transaction_hash"], "ts": x["timestamp"]})
            got += 1
        if got >= 25:
            break
    markets_used.append({"condition_id": cond, "neg_risk": neg, "redeems": got})
    print(cond[:12], "neg_risk", neg, "redeems", got, "total", len(rows), flush=True)
shapes = Counter()
for x in rows:
    j = rpc.post("https://polygon.gateway.tenderly.co", json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionByHash", "params": [x["tx"]]})
    stats[f"rpc_{j.status_code}"] += 1
    t = j.json().get("result") or {}
    to, sel = (t.get("to") or "").lower(), (t.get("input") or "")[:10]
    if to == x["wallet"].lower() and sel == "0x6a761202":
        shape = "safe_exec"
    elif to == RELAY_HUB and sel == "0x405cec67":
        shape = "relay_hub"
    else:
        shape = "other"
    x.update(shape=shape, tx_to=to, selector=sel, tx_from=(t.get("from") or "").lower())
    shapes[(shape, bool(x["neg_risk"]))] += 1
others = Counter((x["tx_to"], x["selector"]) for x in rows if x["shape"] == "other")
out = {"seed": SEED, "markets": markets_used, "n_redeems": len(rows), "distinct_txs": len({x["tx"] for x in rows}),
       "shapes": {f"{k[0]}|neg_risk={k[1]}": v for k, v in shapes.items()},
       "other_to_selector": {f"{k[0]}|{k[1]}": v for k, v in others.most_common(20)}, "calls": dict(stats), "rows": rows}
json.dump(out, open(sys.argv[1], "w"), indent=1)
print("SHAPES", json.dumps(out["shapes"]), "OTHER", json.dumps(out["other_to_selector"]), "N", len(rows), "markets", len(markets_used), "calls", dict(stats))
