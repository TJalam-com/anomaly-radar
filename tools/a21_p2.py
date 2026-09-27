"""G1 items on the frozen M set (results/G0_M_phase1_markets_2026-09-26.csv, include==True):

A21  Gamma closedTime vs on-chain CTF ConditionResolution(conditionId) block time, per market.
     Method: bisect first block with ts >= closedTime (block-ts memoised), then eth_getLogs
     (CTF 0x4d97…6045, topic0 = ConditionResolution, topic1 = conditionId) in 2000-block windows
     expanding outward to +-MAX_SPAN blocks. Reports tx, block, block ts, delta_s = chain - closedTime.
P-2  data-api /v2/trades?condition=&limit=1&taker_only=false per market: HTTP status + rows.

Also records every RPC HTTP status / JSON-RPC error and wall time (rate-limit evidence).
Usage: python tools/a21_p2.py <M.csv> <out.csv>
"""
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime

import httpx
from Crypto.Hash import keccak

RPC = "https://polygon.gateway.tenderly.co"
CTF = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
WIN = 2000
MAX_SPAN = 40000


def k256(s):
    h = keccak.new(digest_bits=256)
    h.update(s.encode())
    return "0x" + h.hexdigest()


assert k256("hello") == "0x1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8", "keccak control"
CR = k256("ConditionResolution(bytes32,address,bytes32,uint256,uint256[])")

stats = Counter()
cli = httpx.Client(timeout=60, headers={"User-Agent": "anomaly-radar/0.1 (read-only research)"})


def rpc(method, params):
    for attempt in range(6):
        t0 = time.time()
        r = cli.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        stats[f"http_{r.status_code}"] += 1
        stats["rpc_seconds"] += time.time() - t0
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        j = r.json()
        if "error" in j:
            stats["jsonrpc_error"] += 1
            raise RuntimeError(f"{method}: {j['error']}")
        return j["result"]
    raise RuntimeError(f"{method}: retries exhausted")


_ts = {}


def block_ts(b):
    if b not in _ts:
        _ts[b] = int(rpc("eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
    return _ts[b]


def first_block_at(ts, lo, hi):
    while lo < hi:
        mid = (lo + hi) // 2
        if block_ts(mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def find_resolution(cond, b0):
    for step in range(MAX_SPAN // WIN):
        wins = [(b0 - WIN // 2, b0 + WIN // 2 - 1)] if step == 0 else \
               [(b0 - WIN // 2 - step * WIN, b0 - WIN // 2 - (step - 1) * WIN - 1),
                (b0 + WIN // 2 + (step - 1) * WIN, b0 + WIN // 2 + step * WIN - 1)]
        logs = []
        for a, z in wins:
            logs += rpc("eth_getLogs", [{"address": CTF, "fromBlock": hex(a), "toBlock": hex(z), "topics": [CR, cond]}])
        if logs:
            return logs, (step + 1) * WIN
    return [], MAX_SPAN


def closed_unix(s):
    s = s.replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def main(m_csv, out_csv):
    rows = [r for r in csv.DictReader(open(m_csv, encoding="utf-8")) if r["include"] == "True"]
    head = int(rpc("eth_blockNumber", []), 16)
    t_start = time.time()
    out = []
    for i, r in enumerate(rows):
        cond = r["condition_id"].lower()
        rec = {"condition_id": cond, "market_id": r["market_id"], "class": r["class"], "closedTime": r["closedTime"]}
        ct = closed_unix(r["closedTime"])
        b0 = first_block_at(ct, 75_000_000, head)
        rec.update(closed_unix=ct, bisect_block=b0)
        try:
            logs, span = find_resolution(cond, b0)
        except RuntimeError as e:
            logs, span = [], -1
            rec["error"] = str(e)[:200]
        rec["resolution_logs"] = len(logs)
        rec["searched_span_blocks"] = span
        if logs:
            lg = logs[0]
            bn = int(lg["blockNumber"], 16)
            d = lg["data"][2:]
            words = [int(d[j:j + 64], 16) for j in range(0, len(d), 64)]
            rec.update(resolution_tx=lg["transactionHash"], resolution_block=bn, resolution_ts=block_ts(bn),
                       delta_s=block_ts(bn) - ct, oracle="0x" + lg["topics"][2][-40:],
                       payouts=json.dumps(words[3:]))
        # P-2
        pr = cli.get("https://data-api.polymarket.com/v2/trades",
                     params={"condition": cond, "limit": 1, "taker_only": "false"})
        stats[f"dataapi_http_{pr.status_code}"] += 1
        rec["p2_http"] = pr.status_code
        rec["p2_rows"] = len(pr.json().get("data", [])) if pr.status_code == 200 else None
        out.append(rec)
        print(i, cond[:12], rec.get("delta_s"), rec["resolution_logs"], rec["p2_rows"], flush=True)
    cols = ["condition_id", "market_id", "class", "closedTime", "closed_unix", "bisect_block", "resolution_logs",
            "searched_span_blocks", "resolution_tx", "resolution_block", "resolution_ts", "delta_s", "oracle",
            "payouts", "p2_http", "p2_rows", "error"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    stats["wall_seconds"] = round(time.time() - t_start, 1)
    stats["rpc_seconds"] = round(stats["rpc_seconds"], 1)
    print("STATS", json.dumps(dict(stats)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
