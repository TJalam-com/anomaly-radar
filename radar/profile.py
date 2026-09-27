"""Per-wallet profiling for S1/S2/S6/S8 (design note r6 Δ6: targeted calls, no full activity walks).

For each profiled wallet (passed_prefilter OR bypass_listed; computed on the SNAP + step-4 views):
  activity  v2/activity?user=&limit=1&sort_direction=ASC   -> first_trade_ts   (asserted <= DESC newest ts)
  stats     v2/user-stats?user=                            -> lifetime volume_usdc (both sides)
  redeem    per winning condition: v2/activity?user=&condition=&type=REDEEM -> first REDEEM; its tx shape via RPC
  dormancy  v2/activity?user=&type=TRADE&start=<redeem_ts+1>&sort_direction=ASC&limit=1 -> next trade after redeem
  transfers tenderly eth_getLogs (USDC.e, USDC, pUSD) from genesis; hop 1 in (to=w) and out (from=w);
            hops 2..max: follow the top-`hop_breadth` non-stop-listed counterparties in the same direction.
Raw responses are stored per wallet as one JSON bundle under data/profiles/PROF-NNN/raw/<wallet>.json
(sha256 in the manifest); derived parquet is built from the bundles only. Resumable: existing bundles are kept.
Statuses: 'ok' | 'empty' | 'unavailable' | 'capped' (transfers above the log cap -> S6 NA 'transfers_unavailable').
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from radar import config, entities
from radar.snapshots import utcnow

DATA_API = config.DATA_API
RPC = "https://polygon.gateway.tenderly.co"
TOKENS = ["0x2791bca1f2de4661ed88a30c99a7a9449aa84174", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
          "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"]
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
RELAY_HUB = "0xd216153c06e857cd7f72665e0af1d7d82172f494"
DIRECT_TARGETS = {"0x4d97dcd97ec945f40cf65f87097ace5ea0476045", "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"}
ERC4337_ENTRYPOINT = "0x0000000071727de22e5e9d8baf0edac6f37da032"
# defaults; the run uses [fetch] from the frozen params file (config/params_frozen_*.toml)
FETCH = {"funding_lower_bound_block": 0, "funding_truncation_window_blocks": 1_000_000,
         "hop1_log_cap": 50_000, "hop_n_hub_cap": 5_000}


class ProfileError(RuntimeError):
    pass


class Clients:
    def __init__(self, api_transport=None, rpc_transport=None, sleep=time.sleep):
        h = {"User-Agent": "anomaly-radar/0.1 (read-only research)"}
        self.api = httpx.Client(transport=api_transport, timeout=120, headers=h)
        self.rpc = httpx.Client(transport=rpc_transport, timeout=180, headers=h)
        self.sleep = sleep
        self.calls = {"api": 0, "rpc": 0, "http_429": 0, "hop_cache_hits": 0}
        self.hop_cache = {}   # (address, direction) -> (logs, status): hop>=2 lookups are shared across wallets

    def get(self, path, params):
        for attempt in range(5):
            r = self.api.get(DATA_API + path, params=params)
            self.calls["api"] += 1
            if r.status_code == 429:
                self.calls["http_429"] += 1
                raise ProfileError("HTTP 429 from data-api: stopping (rate limit)")
            if r.status_code >= 500 and attempt < 4:
                self.sleep(2 ** attempt); continue
            return r.status_code, (r.json() if r.status_code == 200 else {"error": r.text[:300]})
        return r.status_code, {"error": "retries exhausted"}

    def rpc_call(self, method, params):
        for attempt in range(5):
            r = self.rpc.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            self.calls["rpc"] += 1
            if r.status_code == 429:
                self.calls["http_429"] += 1
                raise ProfileError("HTTP 429 from RPC: stopping (rate limit)")
            if r.status_code >= 500 and attempt < 4:
                self.sleep(2 ** attempt); continue
            j = r.json()
            if "error" in j:
                return None, j["error"]
            return j["result"], None
        return None, {"message": "retries exhausted"}


def _pad(a):
    return "0x" + "0" * 24 + a[2:].lower()


TOO_BIG = ("more than", "too many", "limit", "range")


def fetch_logs(cl, address, direction, to_block):
    """All collateral Transfer logs into (in) / out of (out) `address` over [funding_lower_bound_block, to_block]
    (to_block = an integer resolved before the fetch, never 'latest'). Bisects when the node refuses a large result.
    Returns (logs, record); record.subranges lists every leaf range with its status, so completeness can be judged
    from the record alone (contiguous, gap-free, all ok)."""
    lo0 = int(FETCH["funding_lower_bound_block"])
    topics = [TRANSFER, None, _pad(address)] if direction == "in" else [TRANSFER, _pad(address)]
    logs, subs = [], []

    def rec(lo, hi, depth):
        res, err = cl.rpc_call("eth_getLogs", [{"address": TOKENS, "fromBlock": hex(lo), "toBlock": hex(hi), "topics": topics}])
        if err is None:
            logs.extend(res)
            subs.append({"from": lo, "to": hi, "status": "ok", "n": len(res)})
            return True
        if any(t in str(err).lower() for t in TOO_BIG) and depth < 12 and hi > lo:
            mid = (lo + hi) // 2
            return rec(lo, mid, depth + 1) and rec(mid + 1, hi, depth + 1)
        subs.append({"from": lo, "to": hi, "status": "unavailable", "error": str(err)[:200]})
        return False

    ok = rec(lo0, int(to_block), 0)
    status = "ok" if ok else "unavailable"
    return logs, {"address": address.lower(), "direction": direction, "status": status, "n_logs": len(logs),
                  "from_block": lo0, "to_block": int(to_block), "subranges": subs, "fetched_at": utcnow(), "cache_hit": False}


def gap_free(record) -> bool:
    """r14 Δ20: every sub-range ok, sorted ranges contiguous from from_block to the resolved integer to_block."""
    if not isinstance(record.get("to_block"), int) or record.get("status") != "ok":
        return False
    subs = sorted(record.get("subranges") or [], key=lambda x: x["from"])
    if not subs or subs[0]["from"] > record["from_block"] or subs[-1]["to"] != record["to_block"]:
        return False
    return all(x["status"] == "ok" for x in subs) and all(b["from"] == a["to"] + 1 for a, b in zip(subs, subs[1:]))


def classify_shape(tx, wallet):
    to, sel, frm = (tx.get("to") or "").lower(), (tx.get("input") or "")[:10], (tx.get("from") or "").lower()
    w = wallet.lower()
    if to == w and sel == "0x6a761202":
        return "safe_exec"
    if to == RELAY_HUB and sel == "0x405cec67":
        return "relay_hub"
    if frm == w and to in DIRECT_TARGETS:
        return "direct_eoa"          # recorded; whether it counts as user-signed is QA's ruling (signals.USER_SIGNED_SHAPES)
    if to == ERC4337_ENTRYPOINT:
        return "erc4337"
    return "other"


def profile_wallet(cl, wallet, win_conds, stop, P):
    b = {"wallet": wallet, "fetched_at": utcnow(), "calls": {}}
    st, asc = cl.get("/v2/activity", {"user": wallet, "limit": 1, "sort_direction": "ASC"})
    st2, desc = cl.get("/v2/activity", {"user": wallet, "limit": 1})
    b["activity_asc"], b["activity_desc"] = {"status": st, "body": asc}, {"status": st2, "body": desc}
    if st == 200 and st2 == 200 and asc.get("data") and desc.get("data"):
        if asc["data"][0]["timestamp"] > desc["data"][0]["timestamp"]:
            raise ProfileError(f"sort_direction=ASC not honoured for {wallet}: asc {asc['data'][0]['timestamp']} > desc")
    st, stats = cl.get("/v2/user-stats", {"user": wallet})
    b["stats"] = {"status": st, "body": stats}
    b["redeems"] = {}
    for c in win_conds:
        st, red = cl.get("/v2/activity", {"user": wallet, "condition": c, "type": "REDEEM", "limit": 100})
        entry = {"status": st, "body": red}
        rows = red.get("data", []) if st == 200 else []
        if rows:
            first = min(rows, key=lambda x: x["timestamp"])
            tx, err = cl.rpc_call("eth_getTransactionByHash", [first["transaction_hash"]])
            entry["first_tx"] = tx if err is None else {"error": err}
            st3, nxt = cl.get("/v2/activity", {"user": wallet, "type": "TRADE", "start": first["timestamp"] + 1,
                                               "sort_direction": "ASC", "limit": 1})
            entry["next_trade"] = {"status": st3, "body": nxt}
        b["redeems"][c] = entry
    b["transfers"] = {}
    b["fetches"] = []
    head, err = cl.rpc_call("eth_blockNumber", [])
    if err is not None:
        raise ProfileError(f"cannot resolve head block: {err}")
    to_block = int(head, 16)
    blk, err = cl.rpc_call("eth_getBlockByNumber", [hex(to_block), False])
    if err is not None:
        raise ProfileError(f"cannot read head block timestamp: {err}")
    b["to_block"], b["to_block_ts"] = to_block, int(blk["timestamp"], 16)
    breadth = int(P.get("s6_hop_breadth", 0))       # params v3 removed it (s6_max_hops = 1: hops >= 2 are never followed)
    for direction in ("in", "out"):
        frontier, hop, dir_out = [(wallet.lower(), None)], 1, []
        status = "ok"
        while frontier and hop <= int(P["s6_max_hops"]):
            nxt_frontier = []
            for addr, parent in frontier:
                key = (addr, direction)
                if hop >= 2 and key in cl.hop_cache:
                    logs, rec0 = cl.hop_cache[key]
                    record = {**rec0, "cache_hit": True, "cache_fetched_at": rec0["fetched_at"]}
                    cl.calls["hop_cache_hits"] += 1
                else:
                    logs, record = fetch_logs(cl, addr, direction, to_block)
                    record["to_block_ts"] = b["to_block_ts"]
                    if hop == 1 and record["status"] == "ok" and len(logs) > FETCH["hop1_log_cap"]:
                        record["status"] = "capped"
                    if hop >= 2 and len(logs) > FETCH["hop_n_hub_cap"]:
                        record["status"] = "hub"            # design exclusion (r15 Δ24/Δ25): never expanded
                        logs = []
                    if hop >= 2:
                        cl.hop_cache[key] = (logs, record)
                st = record["status"]
                b["fetches"].append({"hop": hop, "parent": parent, "record": record})
                if hop == 1 and st != "ok":
                    status = st
                if hop >= 2 and st != "ok":
                    dir_out.append({"hop": hop, "from_address": addr, "hub_or_error": st, "n": record["n_logs"]})
                    continue
                agg = {}
                for lg in logs:
                    cp = "0x" + lg["topics"][1 if direction == "in" else 2][-40:]
                    blk_n = int(lg["blockNumber"], 16) if lg.get("blockNumber") else None
                    li = int(lg["logIndex"], 16)
                    a = agg.setdefault(cp, {"n": 0, "amount": 0.0, "amount_raw": 0, "first_ts": None, "first_block": None,
                                            "tx": lg["transactionHash"], "log_index": li, "token": lg["address"].lower()})
                    ts = int(lg["blockTimestamp"], 16) if lg.get("blockTimestamp") else None
                    raw = int(lg["data"], 16)
                    a["n"] += 1
                    a["amount_raw"] += raw                    # exact integer sum (6-decimal tokens; C4 checked at start)
                    a["amount"] = a["amount_raw"] / 1e6
                    first_key = (blk_n if blk_n is not None else 1 << 62, li)
                    if a["first_block"] is None or first_key < (a["first_block"], a["log_index"]):
                        a["first_ts"], a["first_block"], a["tx"], a["log_index"], a["token"] = ts, blk_n, lg["transactionHash"], li, lg["address"].lower()
                # r15 Δ30 follow rule: (-amount_raw, first_block, first_log_index, address), independent of RPC order
                cands = sorted((cp for cp in agg if cp not in stop and cp != wallet.lower()),
                               key=lambda cp: (-agg[cp]["amount_raw"], agg[cp]["first_block"] or 0, agg[cp]["log_index"], cp))
                follow = cands[:breadth]
                for cp in sorted(agg):
                    a = agg[cp]
                    dir_out.append({"hop": hop, "via": addr, "counterparty": cp, **a, "amount_raw": str(a["amount_raw"]),
                                    "selected": cp in follow, "stop_listed": cp in stop})
                nxt_frontier += [(cp, addr) for cp in follow]
            frontier, hop = nxt_frontier, hop + 1
        b["transfers"][direction] = {"status": status, "edges": dir_out}
    b["t_snap"] = utcnow()
    return b


def bundle_to_rows(b, infra):
    w = b["wallet"]
    asc, stats = b["activity_asc"], b["stats"]
    act_status = "unavailable" if asc["status"] != 200 else ("ok" if asc["body"].get("data") else "empty")
    ftt = asc["body"]["data"][0]["timestamp"] if act_status == "ok" else None
    st_status = "unavailable" if stats["status"] != 200 else "ok"
    vol = (stats["body"].get("data") or {}).get("all_time_pnl", {}).get("volume_usdc") if st_status == "ok" else None
    tr_status = "ok"
    for d in ("in", "out"):
        s = b["transfers"][d]["status"]
        if s in ("unavailable", "capped"):
            tr_status = "unavailable" if s == "unavailable" else "capped"
    fins = [(e["first_ts"], e.get("first_block")) for e in b["transfers"]["in"]["edges"]
            if e.get("hop") == 1 and e.get("counterparty") and e["counterparty"] not in infra and e.get("first_ts")]
    ff = min(fins) if fins else (None, None)
    # QA funding-window rule: truncated = earliest in-window non-infra inbound within W blocks of the lower bound,
    # OR no in-window inbound although the wallet has trades
    lb, win = FETCH["funding_lower_bound_block"], FETCH["funding_truncation_window_blocks"]
    truncated = (ff[1] is not None and ff[1] <= lb + win) or (ff[0] is None and act_status == "ok")
    prof = (w, ftt, ff[0], vol, act_status, st_status,
            "unavailable" if tr_status in ("unavailable", "capped") else "ok", b["t_snap"], tr_status, ff[1], truncated)
    redeems, after = [], []
    for c, e in b["redeems"].items():
        rows = e["body"].get("data", []) if e["status"] == 200 else []
        if rows:
            first = min(rows, key=lambda x: x["timestamp"])
            tx = e.get("first_tx") or {}
            shape = "unavailable" if "error" in tx else classify_shape(tx, w)
            redeems.append((w, c, first["timestamp"], shape, first["transaction_hash"]))
            nb = e.get("next_trade", {})
            nrows = nb.get("body", {}).get("data", []) if nb.get("status") == 200 else None
            after.append((w, c, nrows[0]["timestamp"] if nrows else None))
    transfers = [(w, d, e["hop"], e["counterparty"], e["token"], e["amount"], e["n"], e["first_ts"], e["tx"], e["log_index"],
                  e.get("via"), e.get("selected"), e.get("stop_listed"), e.get("amount_raw"), e.get("first_block"))
                 for d in ("in", "out") for e in b["transfers"][d]["edges"] if e.get("counterparty")]
    return prof, redeems, after, transfers


def bundle_expansions(b):
    """One row per fetch the writer made or reused for this wallet (r14 Δ20, r15 Δ24/Δ25): completeness inputs."""
    kids = {(d, e["hop"], e["via"]) for d in ("in", "out") for e in b["transfers"][d]["edges"] if e.get("counterparty")}
    rows = []
    for f in b.get("fetches", []):
        r = f["record"]
        rows.append((b["wallet"], r["direction"], f["hop"], f.get("parent"), r["address"], r["status"], r["n_logs"],
                     r.get("to_block"), r.get("to_block_ts"), gap_free(r), bool(r.get("cache_hit")), r.get("cache_fetched_at"),
                     f.get("fetch_file"), (r["direction"], f["hop"], r["address"]) in kids))
    return rows


def finalize(prof_dir: Path, meta: dict) -> dict:
    infra = set(entities.POLYMARKET_INFRA)
    P_, R_, A_, T_, E_ = [], [], [], [], []
    files = []
    for f in sorted((prof_dir / "raw").glob("*.json")):
        raw = f.read_bytes()
        b = json.loads(raw)
        files.append({"path": f.relative_to(prof_dir).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                      "kind": "bundle", "writer_id": b.get("writer_id")})
        p, r, a, t = bundle_to_rows(b, infra)
        P_.append(p); R_ += r; A_ += a; T_ += t; E_ += bundle_expansions(b)
    for f in sorted((prof_dir / "fetch").glob("*.json")) if (prof_dir / "fetch").exists() else []:
        raw = f.read_bytes()
        files.append({"path": f.relative_to(prof_dir).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                      "kind": "fetch", "writer_id": json.loads(raw).get("writer_id")})
    L_, Y_ = [], []
    for f in sorted((prof_dir / "lookups").glob("*.json")) if (prof_dir / "lookups").exists() else []:
        raw = f.read_bytes()
        r = json.loads(raw)
        files.append({"path": f.relative_to(prof_dir).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                      "kind": "lookup", "writer_id": r.get("writer_id")})
        L_.append(r)
        Y_ += [(r["address"], y, fb, (r.get("first_ts") or {}).get(y)) for y, fb in sorted(r["first_blocks"].items())]
    d = prof_dir / "derived"
    d.mkdir(exist_ok=True)
    ts = lambda u: None if u is None else int(u)
    tables = {
        "wallet_profile": pa.table({
            "proxy_wallet": [x[0] for x in P_], "first_trade_unix": pa.array([ts(x[1]) for x in P_], pa.int64()),
            "first_funding_unix": pa.array([ts(x[2]) for x in P_], pa.int64()),
            "lifetime_volume_usdc": pa.array([x[3] for x in P_], pa.float64()), "activity_status": [x[4] for x in P_],
            "stats_status": [x[5] for x in P_], "transfers_status": [x[6] for x in P_], "t_snap": [x[7] for x in P_],
            "transfers_status_detail": [x[8] for x in P_], "first_funding_block": pa.array([x[9] for x in P_], pa.int64()),
            "funding_truncated": pa.array([x[10] for x in P_], pa.bool_()),
            "lower_bound_ts_unix": pa.array([meta.get("lower_bound_block_ts")] * len(P_), pa.int64())}),
        "redeems": pa.table({"proxy_wallet": [x[0] for x in R_], "condition_id": [x[1] for x in R_],
                             "ts_unix": pa.array([x[2] for x in R_], pa.int64()), "shape": [x[3] for x in R_],
                             "tx_hash": [x[4] for x in R_]}),
        "trade_after": pa.table({"proxy_wallet": [x[0] for x in A_], "condition_id": [x[1] for x in A_],
                                 "next_trade_unix": pa.array([x[2] for x in A_], pa.int64())}),
        "transfers": pa.table({"proxy_wallet": [x[0] for x in T_], "direction": [x[1] for x in T_],
                               "hop": pa.array([x[2] for x in T_], pa.int32()), "counterparty": [x[3] for x in T_],
                               "token": [x[4] for x in T_], "amount": pa.array([x[5] for x in T_], pa.float64()),
                               "n_logs": pa.array([x[6] for x in T_], pa.int64()),
                               "first_ts_unix": pa.array([x[7] for x in T_], pa.int64()),
                               "tx_hash": [x[8] for x in T_], "log_index": pa.array([x[9] for x in T_], pa.int64()),
                               "via": [x[10] if len(x) > 10 else None for x in T_],
                               "selected": pa.array([x[11] if len(x) > 11 else None for x in T_], pa.bool_()),
                               "stop_listed": pa.array([x[12] if len(x) > 12 else None for x in T_], pa.bool_()),
                               "amount_raw": [x[13] if len(x) > 13 else None for x in T_],
                               "first_block": pa.array([x[14] if len(x) > 14 else None for x in T_], pa.int64())}),
        "expansions": pa.table({"proxy_wallet": [x[0] for x in E_], "direction": [x[1] for x in E_],
                                "hop": pa.array([x[2] for x in E_], pa.int32()), "parent": [x[3] for x in E_],
                                "address": [x[4] for x in E_], "status": [x[5] for x in E_],
                                "n_logs": pa.array([x[6] for x in E_], pa.int64()), "to_block": pa.array([x[7] for x in E_], pa.int64()),
                                "to_block_ts": pa.array([x[8] for x in E_], pa.int64()), "gap_free": pa.array([x[9] for x in E_], pa.bool_()),
                                "cache_hit": pa.array([x[10] for x in E_], pa.bool_()), "cache_fetched_at": [x[11] for x in E_],
                                "fetch_file": [x[12] for x in E_], "has_child_edges": pa.array([x[13] for x in E_], pa.bool_())}),
        "lookups": pa.table({"address": [r["address"] for r in L_], "status": [r["status"] for r in L_],
                             "capped": pa.array([bool(r["capped"]) for r in L_], pa.bool_()),
                             "cap_block": pa.array([r["cap_block"] for r in L_], pa.int64()), "cap_ts": pa.array([r.get("cap_ts") for r in L_], pa.int64()),
                             "from_block": pa.array([r["from_block"] for r in L_], pa.int64()), "to_block": pa.array([r["to_block"] for r in L_], pa.int64()),
                             "n_distinct": pa.array([r["n_distinct"] for r in L_], pa.int64()), "cap": pa.array([r["cap"] for r in L_], pa.int64()),
                             "subranges_json": [json.dumps(r["subranges"], sort_keys=True) for r in L_],
                             "n_sharers_fetch": pa.array([r.get("n_sharers") for r in L_], pa.int64()),
                             "n_natural_sharers_fetch": pa.array([r.get("n_natural_sharers") for r in L_], pa.int64())}),
        "lookup_y": pa.table({"address": [x[0] for x in Y_], "y": [x[1] for x in Y_], "first_block": pa.array([x[2] for x in Y_], pa.int64()),
                              "first_ts": pa.array([x[3] for x in Y_], pa.int64())}),
    }
    derived = []
    for name, tbl in tables.items():
        p = d / f"{name}.parquet"
        pq.write_table(tbl, p)
        derived.append({"file": p.relative_to(prof_dir).as_posix(), "rows": tbl.num_rows,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    body = json.dumps({**meta, "finished_at": utcnow(), "files": files, "derived": derived}, indent=1, sort_keys=True).encode()
    (prof_dir / "manifest.json").write_bytes(body)
    return {"manifest": str(prof_dir / "manifest.json"), "manifest_sha256": hashlib.sha256(body).hexdigest(),
            "wallets": len(P_), "derived": derived}


def check_v3_fetch_params(P: dict):
    """Planner V1-6: under params v3 (S6 C1) the writer fetches hop-1 transfers + activity lookups only; hop >= 2 is never expanded."""
    if P.get("s6_rule") == "C1" and int(P["s6_max_hops"]) != 1:
        raise ProfileError("params v3 (S6 C1) fetches hop 1 only: s6_max_hops must be 1")


def targets(snap_dir: Path, P: dict, bypass: list[str]):
    """Profiled wallets + their winning conditions, from the same SQL as the signals (no duplication of the rule)."""
    from radar import scoring_view, signals
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'"); con.execute("SET threads=1")
    con.execute(f"SET temp_directory='{config.TMP_DIR.as_posix()}'")
    scoring_view.build_scoring_views(con, snap_dir)
    D = (snap_dir / "derived").as_posix()
    con.execute(f"CREATE VIEW trades_all_any AS SELECT * FROM '{D}/trades.parquet' WHERE walk = 'all'")
    con.execute(f"CREATE VIEW tokens_v AS SELECT * FROM '{D}/tokens.parquet'")
    con.execute("CREATE TABLE scope_map AS SELECT 'all' AS scope, condition_id FROM markets_r")
    con.execute("CREATE TABLE bypass(proxy_wallet TEXT)")
    for w in bypass:
        con.execute("INSERT INTO bypass VALUES (?)", [w.lower()])
    signals.universe(con, P)
    # exact net position (QA determinism ruling 2026-09-27): same helper as signals.s5/s8, so an exact-zero
    # position is never a winning bet here either
    rows = con.execute(f"""WITH prof AS (SELECT proxy_wallet, passed_prefilter FROM universe_t WHERE scope = 'all' AND profiled),
        wb AS (SELECT f.proxy_wallet, f.condition_id, f.token_id,
                      sum(CASE WHEN f.side = 'BUY' THEN {signals.sz()} ELSE -{signals.sz()} END) AS pos
               FROM signal_fills f JOIN prof USING (proxy_wallet) WHERE f.walk = 'all' GROUP BY 1, 2, 3)
        SELECT prof.proxy_wallet, list(DISTINCT wb.condition_id) FILTER (WHERE wb.condition_id IS NOT NULL), bool_or(prof.passed_prefilter)
        FROM prof LEFT JOIN (wb JOIN tokens_v k USING (token_id) JOIN markets_r m ON m.condition_id = wb.condition_id)
             ON wb.proxy_wallet = prof.proxy_wallet AND wb.pos > 0 AND NOT m.void AND k.outcome_index = m.chain_winner_index
        GROUP BY 1 ORDER BY 1""").fetchall()
    return [(w, c or [], bool(n)) for w, c, n in rows]      # n = natural (passed_prefilter); bypass-only wallets are False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True)
    ap.add_argument("--prof", required=True, help="profile dir, e.g. data/profiles/PROF-001 (resumable)")
    ap.add_argument("--params", required=True, help="frozen params file (config/params_frozen_*.toml): [params] + [fetch]")
    ap.add_argument("--bypass-list", help="opaque wallet list (header column matching wallet|address)")
    ap.add_argument("--limit", type=int, help="profile only the first N targets (budget probes)")
    ap.add_argument("--workers", type=int, default=1, help="parallel wallets (QA: at most 3); shared hop cache, stop on first 429")
    ap.add_argument("--prof-id", required=True, help="profile id as pinned in WRITER_PINS.md, e.g. PROF-002")
    ap.add_argument("--pins", default=str(config.APP_DIR.parent / "ledger" / "WRITER_PINS.md"), help="QA-held pin ledger")
    ap.add_argument("--print-writer-id", action="store_true", help="print this build's writer_id + closure and exit (no fetch)")
    a = ap.parse_args(argv)
    from radar import scoring_view, signals, writer_id as wid_mod   # noqa: F401  eager: the closure must see every module
    closure_items = wid_mod.closure(config.APP_DIR, Path(a.params))
    WID = wid_mod.writer_id(closure_items)
    if a.print_writer_id:
        print(json.dumps({"writer_id": WID, "closure": closure_items}, indent=1))
        return 0
    pins_sha = wid_mod.require_pinned(Path(a.pins), a.prof_id, WID)
    import tomllib, csv, io
    praw = Path(a.params).read_bytes()
    pcfg = tomllib.loads(praw.decode("utf-8"))
    P = pcfg["params"]
    FETCH.update({k: v for k, v in pcfg.get("fetch", {}).items() if k in FETCH})
    bypass, bmeta = [], None
    if a.bypass_list:
        raw = Path(a.bypass_list).read_bytes()
        rd = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        col = next((c for c in rd.fieldnames if "wallet" in c.lower() or "address" in c.lower()), None)
        if col is None:
            raise ProfileError("bypass list has no wallet/address column")
        bypass = sorted({r[col].strip().lower() for r in rd if r[col].strip()})
        bmeta = {"path": a.bypass_list, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(bypass)}
    check_v3_fetch_params(P)
    prof = Path(a.prof)
    (prof / "raw").mkdir(parents=True, exist_ok=True)
    tg = targets(Path(a.snap), P, bypass)
    if a.limit:
        tg = tg[:a.limit]
    stop = {r[0] for r in entities.rows()}
    cl = Clients()
    decimals = {}
    for t in TOKENS:                                          # C4: amounts are divided by 1e6; refuse any other scale
        res, err = cl.rpc_call("eth_call", [{"to": t, "data": "0x313ce567"}, "latest"])
        if err is not None or not res or int(res, 16) != 6:
            raise ProfileError(f"token {t} decimals() = {res!r} (err {err}); the writer assumes 6")
        decimals[t] = int(res, 16)
    blk, err = cl.rpc_call("eth_getBlockByNumber", [hex(FETCH["funding_lower_bound_block"]), False])
    if err is not None:
        raise ProfileError(f"cannot read lower-bound block: {err}")
    lb_ts = int(blk["timestamp"], 16)
    if a.workers > 3:
        raise ProfileError("QA ruling: at most 3 workers")
    import threading
    from concurrent.futures import ThreadPoolExecutor
    stop_flag = threading.Event()
    tls = threading.local()
    lock = threading.Lock()
    done = [0]
    worker_clients = []
    todo = [(i, w, wins) for i, (w, wins, _nat) in enumerate(tg) if not (prof / "raw" / f"{w}.json").exists()]

    def work(item):
        i, w, wins = item
        if stop_flag.is_set():
            return
        if not hasattr(tls, "cl"):
            tls.cl = Clients()
            tls.cl.hop_cache = cl.hop_cache          # shared across workers (dict ops are atomic in CPython)
            with lock:
                worker_clients.append(tls.cl)
        try:
            b = profile_wallet(tls.cl, w, wins, stop, P)
        except ProfileError:
            stop_flag.set()
            raise
        wid_mod.assert_unchanged(closure_items, config.APP_DIR, Path(a.params))
        b["writer_id"] = WID
        (prof / "fetch").mkdir(exist_ok=True)
        for f in b["fetches"]:
            r = f["record"]
            name = f"fetch/{r['address']}_{r['direction']}_{r['to_block']}.json"
            f["fetch_file"] = name
            target = prof / name
            if not r.get("cache_hit") and not target.exists():
                part = target.with_suffix(".json.part")
                part.write_bytes(json.dumps({**r, "writer_id": WID}, sort_keys=True).encode())
                part.replace(target)
        tmp = prof / "raw" / f"{w}.json.part"
        tmp.write_bytes(json.dumps(b, sort_keys=True).encode())
        tmp.replace(prof / "raw" / f"{w}.json")      # atomic: a partial bundle never looks complete
        with lock:
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"[{done[0]}/{len(todo)}] {utcnow()} cache_entries {len(cl.hop_cache)}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max(1, a.workers)) as ex:
        for fut in [ex.submit(work, it) for it in todo]:
            fut.result()
    lk_meta, snap_block, t_snap = None, None, None
    if P.get("s6_rule") == "C1":      # params v3: activity lookups for the S6 lookup set (D1 r16b §2), after all hop-1 fetches
        from datetime import datetime as _dt
        from radar import activity
        fin = json.loads((Path(a.snap) / "manifest.json").read_bytes())["finished_at"]
        t_snap = int(_dt.fromisoformat(fin.replace("Z", "+00:00")).timestamp())
        h, err = cl.rpc_call("eth_blockNumber", [])
        if err is not None:
            raise ProfileError(f"cannot resolve head block for the lookups: {err}")
        head = int(h, 16)
        snap_block = activity.block_before(cl, t_snap, head)          # block(SNAP) = last block with ts < SNAP instant (strict)
        natural = {w for w, _wins, nat in tg if nat}
        edges = []
        for f in sorted((prof / "raw").glob("*.json")):
            bb = json.loads(f.read_bytes())
            edges += [(bb["wallet"], e["counterparty"]) for d in ("in", "out") for e in bb["transfers"][d]["edges"]
                      if e.get("hop") == 1 and e.get("counterparty")]
        lset = activity.lookup_set(edges, natural, {x.lower() for x in stop})
        wid_mod.assert_unchanged(closure_items, config.APP_DIR, Path(a.params))
        lk_meta = activity.run_lookups(cl, prof, lset, head, int(P["s6_activity_cap"]), WID, stop_flag)
        lk_meta.update(lookup_head_block=head, activity_cap=int(P["s6_activity_cap"]))
    meta = {"snap": str(a.snap), "snap_manifest_sha256": hashlib.sha256((Path(a.snap) / "manifest.json").read_bytes()).hexdigest(),
            "step4_manifest_sha256": hashlib.sha256((Path(a.snap) / "step4" / "manifest.json").read_bytes()).hexdigest(),
            "params_file": {"path": a.params, "sha256": hashlib.sha256(praw).hexdigest()}, "fetch": FETCH,
            "lower_bound_block_ts": lb_ts, "bypass_list": bmeta, "targets": len(tg), "workers": a.workers,
            "prof_id": a.prof_id, "writer_id": WID, "writer_closure": closure_items, "writer_pins_sha256": pins_sha,
            "follow_rule": "top s6_hop_breadth by (-amount_raw exact, first_block, first_log_index, address); stop set and wallet excluded",
            "token_decimals": decimals, "bundle_path": "raw/<wallet>.json", "stop_set_sha256": hashlib.sha256(
                json.dumps(sorted(stop)).encode()).hexdigest(),
            "calls_this_session": {k: cl.calls[k] + sum(c.calls[k] for c in worker_clients) for k in cl.calls},
            "snap_block": snap_block, "snap_instant_unix": t_snap, "lookups": lk_meta}
    print(json.dumps(finalize(prof, meta), indent=1))


if __name__ == "__main__":
    sys.exit(main())
