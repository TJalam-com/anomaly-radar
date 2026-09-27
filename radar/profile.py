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


def get_logs(cl, address, direction, lo=None, hi=None, depth=0, acc=None):
    """All collateral Transfer logs into (in) or out of (out) `address`; bisects the block range when the node
    refuses a too-large result. Returns (logs, status)."""
    acc = [] if acc is None else acc
    lo = FETCH["funding_lower_bound_block"] if lo is None else lo
    topics = [TRANSFER, None, _pad(address)] if direction == "in" else [TRANSFER, _pad(address)]
    res, err = cl.rpc_call("eth_getLogs", [{"address": TOKENS, "fromBlock": hex(lo),
                                            "toBlock": hex(hi) if hi is not None else "latest", "topics": topics}])
    if err is None:
        acc.extend(res)
        return acc, ("capped" if len(acc) > FETCH["hop1_log_cap"] else "ok")
    msg = str(err).lower()
    if ("more than" in msg or "too many" in msg or "limit" in msg or "range" in msg) and depth < 12:
        if hi is None:
            head, e2 = cl.rpc_call("eth_blockNumber", [])
            if e2 is not None:
                return acc, "unavailable"
            hi = int(head, 16)
        mid = (lo + hi) // 2
        acc, st = get_logs(cl, address, direction, lo, mid, depth + 1, acc)
        if st != "ok":
            return acc, st
        return get_logs(cl, address, direction, mid + 1, hi, depth + 1, acc)
    return acc, "unavailable"


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
    for direction in ("in", "out"):
        frontier, hop, dir_out = [wallet], 1, []
        status = "ok"
        while frontier and hop <= int(P["s6_max_hops"]):
            nxt_frontier = []
            for addr in frontier:
                key = (addr.lower(), direction)
                if hop >= 2 and key in cl.hop_cache:
                    logs, st = cl.hop_cache[key]
                    cl.calls["hop_cache_hits"] += 1
                else:
                    logs, st = get_logs(cl, addr, direction)
                    if hop >= 2:
                        cl.hop_cache[key] = (logs if len(logs) <= FETCH["hop_n_hub_cap"] else [], st if len(logs) <= FETCH["hop_n_hub_cap"] else "hub")
                        if len(logs) > FETCH["hop_n_hub_cap"]:
                            logs, st = [], "hub"
                if hop == 1 and st != "ok":
                    status = st
                if hop >= 2 and (st != "ok" or len(logs) > FETCH["hop_n_hub_cap"]):  # 'hub' from cache lands here too
                    dir_out.append({"hop": hop, "from_address": addr, "hub_or_error": st, "n": len(logs)})
                    continue
                agg = {}
                for lg in logs:
                    cp = "0x" + lg["topics"][1 if direction == "in" else 2][-40:]
                    a = agg.setdefault(cp, {"n": 0, "amount": 0.0, "first_ts": None, "first_block": None, "tx": lg["transactionHash"],
                                            "log_index": int(lg["logIndex"], 16), "token": lg["address"].lower()})
                    ts = int(lg["blockTimestamp"], 16) if lg.get("blockTimestamp") else None
                    a["n"] += 1
                    a["amount"] += int(lg["data"], 16) / 1e6
                    blk = int(lg["blockNumber"], 16) if lg.get("blockNumber") else None
                    if ts is not None and (a["first_ts"] is None or ts < a["first_ts"]):
                        a["first_ts"], a["first_block"], a["tx"], a["log_index"], a["token"] = ts, blk, lg["transactionHash"], int(lg["logIndex"], 16), lg["address"].lower()
                for cp, a in agg.items():
                    dir_out.append({"hop": hop, "via": addr, "counterparty": cp, **a})
                follow = sorted((cp for cp in agg if cp not in stop and cp != wallet.lower()),
                                key=lambda cp: -agg[cp]["amount"])[:int(P["s6_hop_breadth"])]
                nxt_frontier += follow
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
    transfers = [(w, d, e["hop"], e["counterparty"], e["token"], e["amount"], e["n"], e["first_ts"], e["tx"], e["log_index"])
                 for d in ("in", "out") for e in b["transfers"][d]["edges"] if e.get("counterparty")]
    return prof, redeems, after, transfers


def finalize(prof_dir: Path, meta: dict) -> dict:
    infra = set(entities.POLYMARKET_INFRA)
    P_, R_, A_, T_ = [], [], [], []
    files = []
    for f in sorted((prof_dir / "raw").glob("*.json")):
        raw = f.read_bytes()
        files.append({"path": f.relative_to(prof_dir).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        p, r, a, t = bundle_to_rows(json.loads(raw), infra)
        P_.append(p); R_ += r; A_ += a; T_ += t
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
                               "tx_hash": [x[8] for x in T_], "log_index": pa.array([x[9] for x in T_], pa.int64())}),
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
    rows = con.execute(f"""WITH prof AS (SELECT proxy_wallet FROM universe_t WHERE scope = 'all' AND profiled),
        wb AS (SELECT f.proxy_wallet, f.condition_id, f.token_id,
                      sum(CASE WHEN f.side = 'BUY' THEN {signals.sz()} ELSE -{signals.sz()} END) AS pos
               FROM signal_fills f JOIN prof USING (proxy_wallet) WHERE f.walk = 'all' GROUP BY 1, 2, 3)
        SELECT prof.proxy_wallet, list(DISTINCT wb.condition_id) FILTER (WHERE wb.condition_id IS NOT NULL)
        FROM prof LEFT JOIN (wb JOIN tokens_v k USING (token_id) JOIN markets_r m ON m.condition_id = wb.condition_id)
             ON wb.proxy_wallet = prof.proxy_wallet AND wb.pos > 0 AND NOT m.void AND k.outcome_index = m.chain_winner_index
        GROUP BY 1 ORDER BY 1""").fetchall()
    return [(w, c or []) for w, c in rows]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True)
    ap.add_argument("--prof", required=True, help="profile dir, e.g. data/profiles/PROF-001 (resumable)")
    ap.add_argument("--params", required=True, help="frozen params file (config/params_frozen_*.toml): [params] + [fetch]")
    ap.add_argument("--bypass-list", help="opaque wallet list (header column matching wallet|address)")
    ap.add_argument("--limit", type=int, help="profile only the first N targets (budget probes)")
    ap.add_argument("--workers", type=int, default=1, help="parallel wallets (QA: at most 3); shared hop cache, stop on first 429")
    a = ap.parse_args(argv)
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
    prof = Path(a.prof)
    (prof / "raw").mkdir(parents=True, exist_ok=True)
    tg = targets(Path(a.snap), P, bypass)
    if a.limit:
        tg = tg[:a.limit]
    stop = {r[0] for r in entities.rows()}
    cl = Clients()
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
    todo = [(i, w, wins) for i, (w, wins) in enumerate(tg) if not (prof / "raw" / f"{w}.json").exists()]

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
    meta = {"snap": str(a.snap), "snap_manifest_sha256": hashlib.sha256((Path(a.snap) / "manifest.json").read_bytes()).hexdigest(),
            "step4_manifest_sha256": hashlib.sha256((Path(a.snap) / "step4" / "manifest.json").read_bytes()).hexdigest(),
            "params_file": {"path": a.params, "sha256": hashlib.sha256(praw).hexdigest()}, "fetch": FETCH,
            "lower_bound_block_ts": lb_ts, "bypass_list": bmeta, "targets": len(tg), "workers": a.workers,
            "calls_this_session": {k: cl.calls[k] + sum(c.calls[k] for c in worker_clients) for k in cl.calls}}
    print(json.dumps(finalize(prof, meta), indent=1))


if __name__ == "__main__":
    sys.exit(main())
