"""D4: completeness check of SNAP-003 taker-walk rows against on-chain OrderFilled logs, by sampled block windows.

Mapping under test (calibrated on known-good markets first):
  one taker-walk API row  <->  one OrderFilled log on CTF Exchange V1 whose `taker` topic == the exchange address
  (the taker-order fill that matchOrders emits once per match) and whose asset id is one of the market's tokens.
  side: makerAssetId == 0 (taker pays collateral) -> BUY of takerAssetId, size = takerAmountFilled / 1e6
        takerAssetId == 0 (taker receives collateral) -> SELL of makerAssetId, size = makerAmountFilled / 1e6

Per window of W blocks: chain set = {(tx, token, side, size)} from logs; API set = taker rows with block-ts strictly
inside (ts(first block), ts(last block)); chain logs restricted to blocks with ts in the same open interval.
Compared as multisets, both directions. Windows: K centred on random API fills + K uniform over the market lifetime
(Gamma acceptingOrdersTimestamp or createdAt -> closedTime + 1 h), fixed seed.

Usage: python tools/chain_fill_check.py <condition_id> [--k 10] [--w 50] [--seed 7] [--out results.json]
"""
import argparse
import pathlib
import json
import random
import time
from collections import Counter

import duckdb
import httpx
from Crypto.Hash import keccak

RPC = "https://polygon.gateway.tenderly.co"
EX_V1 = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"      # Polymarket: CTF Exchange (neg_risk = false, pre-migration)
EX_NEGRISK = "0xc5d563a36ae78145c45a50134d48a1215220f80a"  # Polymarket: Neg Risk CTF Exchange (neg_risk = true); mapping calibrated separately
SNAP = "data/snapshots/SNAP-003/derived"


def select_exchange(neg_risk: bool, override: str | None = None) -> str:
    """Exchange whose taker-order OrderFilled logs map 1:1 to taker-walk rows (calibrated: V1 on 4 markets, NegRisk on 2).
    An override exists only for negative controls and is recorded in the output."""
    if override:
        return override.lower()
    return EX_NEGRISK if neg_risk else EX_V1


def k256(s):
    h = keccak.new(digest_bits=256)
    h.update(s.encode())
    return "0x" + h.hexdigest()


assert k256("hello") == "0x1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"
OF = k256("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)")
stats = Counter()
cli = httpx.Client(timeout=120, headers={"User-Agent": "anomaly-radar/0.1 (read-only research)"})


def rpc(method, params):
    for attempt in range(6):
        r = cli.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        stats[f"http_{r.status_code}"] += 1
        stats["bytes"] += len(r.content)
        if r.status_code == 429:
            raise RuntimeError("HTTP 429 from RPC: stopping (QA: stop on first 429)")
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        j = r.json()
        if "error" in j:
            stats["jsonrpc_error"] += 1
            raise RuntimeError(f"{method}: {j['error']}")
        return j["result"]
    raise RuntimeError("retries exhausted")


_ts = {}


def bts(b):
    if b not in _ts:
        _ts[b] = int(rpc("eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
    return _ts[b]


_anchor = []


def block_at(ts, lo=75_000_000, hi=None):
    """First block with timestamp >= ts. After the first full bisection, bracket around an estimate from the
    anchor (Polygon ~2 s/block) and bisect only inside the bracket; the bracket is verified, else full range."""
    hi = hi or int(rpc("eth_blockNumber", []), 16)
    if _anchor:
        ab, ats = _anchor[0]
        est = ab + (ts - ats) // 2
        l2, h2 = max(lo, est - 3000), min(hi, est + 3000)
        if bts(l2) < ts <= bts(h2):
            lo, hi = l2, h2
    while lo < hi:
        mid = (lo + hi) // 2
        if bts(mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    if not _anchor:
        _anchor.append((lo, bts(lo)))
    return lo


def decode(lg):
    d = lg["data"][2:]
    w = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
    maker_asset, taker_asset, maker_amt, taker_amt = w[0], w[1], w[2], w[3]
    if maker_asset == 0:
        return str(taker_asset), "BUY", round(taker_amt / 1e6, 6)
    if taker_asset == 0:
        return str(maker_asset), "SELL", round(maker_amt / 1e6, 6)
    return None  # token-for-token (should not occur for a taker order); counted separately


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("condition")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--k-centred", type=int, default=None, help="default --k")
    ap.add_argument("--k-uniform", type=int, default=None, help="default --k")
    ap.add_argument("--exchange", default=None, help="override exchange address (negative controls only; recorded)")
    ap.add_argument("--w", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out")
    a = ap.parse_args()
    cond = a.condition.lower()
    con = duckdb.connect()
    con.execute("SET memory_limit='300MB'"); con.execute("SET threads=1")
    toks = {t for (t,) in con.execute(f"SELECT token_id FROM '{SNAP}/tokens.parquet' WHERE condition_id = ?", [cond]).fetchall()}
    neg = con.execute(f"SELECT neg_risk FROM '{SNAP}/markets.parquet' WHERE condition_id = ?", [cond]).fetchone()[0]
    EX = select_exchange(bool(neg), a.exchange)
    EXT = "0x" + "0" * 24 + EX[2:]
    api = con.execute(f"SELECT epoch(ts)::BIGINT, tx_hash, token_id, side, size FROM '{SNAP}/trades.parquet' "
                      "WHERE walk = 'taker' AND condition_id = ? ORDER BY 1", [cond]).fetchall()
    assert toks and api, "no tokens / no API taker rows"
    rng = random.Random(a.seed)
    # Lifetime for uniform windows comes from Gamma market fields, NOT from the API fills (an API-derived span
    # could not see missing fills at its own edges): acceptingOrdersTimestamp (else createdAt) -> closedTime + 1 h.
    lo, hi = con.execute(f"SELECT epoch(coalesce(accepting_orders_at, created_at))::BIGINT, epoch(closed_at)::BIGINT "
                         f"FROM '{SNAP}/markets.parquet' WHERE condition_id = ?", [cond]).fetchone()
    t_lo, t_hi = lo, hi + 3600
    kc = a.k if a.k_centred is None else a.k_centred
    ku = a.k if a.k_uniform is None else a.k_uniform
    centres = [("api_fill", rng.choice(api)[0]) for _ in range(kc)] + [("uniform", rng.randint(t_lo, t_hi)) for _ in range(ku)]
    head = int(rpc("eth_blockNumber", []), 16)
    windows, tot = [], Counter()
    for kind, ts in centres:
        b0 = block_at(ts, hi=head) - a.w // 2
        b1 = b0 + a.w - 1
        s0, s1 = bts(b0), bts(b1)
        logs = rpc("eth_getLogs", [{"address": EX, "fromBlock": hex(b0), "toBlock": hex(b1), "topics": [OF, None, None, EXT]}])
        chain = Counter()
        other = 0
        for lg in logs:
            dec = decode(lg)
            if dec is None:
                other += 1
                continue
            tok, side, size = dec
            if tok not in toks:
                continue
            bt = bts(int(lg["blockNumber"], 16))  # block ts fetched only for this market's fills
            if s0 < bt < s1:
                chain[(lg["transactionHash"], tok, side, size)] += 1
        apiw = Counter((tx, tok, side, round(size, 6)) for t, tx, tok, side, size in api if s0 < t < s1)
        miss_api = chain - apiw      # on chain, not in API
        miss_chain = apiw - chain    # in API, not on chain
        w = {"kind": kind, "blocks": [b0, b1], "ts": [s0, s1], "chain_logs_window": len(logs), "token_for_token": other,
             "chain_fills": sum(chain.values()), "api_rows": sum(apiw.values()),
             "on_chain_not_api": sum(miss_api.values()), "in_api_not_chain": sum(miss_chain.values()),
             "examples_on_chain_not_api": [list(x) for x in list(miss_api)[:3]],
             "examples_in_api_not_chain": [list(x) for x in list(miss_chain)[:3]]}
        windows.append(w)
        for key in ("chain_fills", "api_rows", "on_chain_not_api", "in_api_not_chain", "token_for_token"):
            tot[key] += w[key]
        print(json.dumps({k: w[k] for k in ("kind", "blocks", "chain_fills", "api_rows", "on_chain_not_api", "in_api_not_chain")}), flush=True)
    # Detection limits (SAMPLED method, pre-declared terms from QA):
    #  per-fill: if a fraction p of on-chain fills were missing from the API independently at random, the chance that
    #  none of the n chain fills seen in the windows is missing is (1-p)^n; p95 = 1 - 0.05**(1/n) is the smallest p
    #  detected with >= 95% probability.
    #  per-gap: a contiguous API outage covering a fraction g of the fill span is hit by at least one of K uniform
    #  windows with prob 1-(1-g)^K (window width neglected); g95 = 1 - 0.05**(1/K).
    n_uni = sum(w["chain_fills"] for w in windows if w["kind"] == "uniform")
    n_all = sum(w["chain_fills"] for w in windows)
    p95 = lambda n: (1 - 0.05 ** (1 / n)) if n else None
    limits = {"chain_fills_in_uniform_windows": n_uni, "chain_fills_in_all_windows": n_all,
              "p95_missing_fill_rate_uniform": p95(n_uni), "p95_missing_fill_rate_all": p95(n_all),
              "g95_outage_fraction_of_span": (1 - 0.05 ** (1 / ku)) if ku else None, "span_seconds": t_hi - t_lo,
              "blocks_sampled": (kc + ku) * a.w}
    print("LIMITS", json.dumps(limits))
    import hashlib
    tool_sha = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    out = {"tool_sha256": tool_sha, "condition": cond, "neg_risk": bool(neg), "exchange": EX, "exchange_overridden": bool(a.exchange), "k": a.k, "k_centred": kc, "k_uniform": ku, "w": a.w, "seed": a.seed, "api_taker_rows_total": len(api), "limits": limits,
           "label": "SAMPLED (not a full count)",
           "windows_with_fills": sum(1 for w in windows if w["chain_fills"] or w["api_rows"]),
           "totals": dict(tot), "rpc": dict(stats), "windows": windows}
    print("TOTALS", json.dumps(out["totals"]), "RPC", json.dumps(out["rpc"]))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
