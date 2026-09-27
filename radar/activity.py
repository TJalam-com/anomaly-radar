"""S6 v3 activity lookups (params v3 [fetch] activity_*; D1 notes r16b §2, r16c, r16d).

activity(X, t) = #distinct Y != X with >= 1 traced-token Transfer X->Y or Y->X at a block <= block(t), block(t) = the last block with
timestamp < t (strict). Lookup: eth_getLogs over the 3 traced tokens with two topic streams (topic1 = X, topic2 = X), ascending from the
funding lower bound with range bisection; per bisected leaf range BOTH streams are fetched, merged by (block, log_index), and only then
accumulated, so cap_block and first_block(Y) come from one global order (R16A-6). Stops at C distinct Y (capped).
A record is complete at t when every fetched leaf is ok and contiguous from the lower bound, and either it is capped or its to_block
covers block(t). Incomplete or missing -> the counterparty cannot link (s6_activity_unknown = no_link)."""
import json
from pathlib import Path

from radar.knee_g import complete_at, lookup_gap_free  # noqa: F401  (pure rules live with the as-of rule)
from radar.profile import FETCH, TOKENS, TOO_BIG, TRANSFER, _pad
from radar.snapshots import utcnow


def _key(lg):
    return int(lg["blockNumber"], 16), int(lg["logIndex"], 16)


def _addr(topic):
    return "0x" + topic[-40:].lower()


def lookup_activity(cl, address, to_block, cap):
    import time as _time
    X = address.lower()
    lo0 = int(FETCH["funding_lower_bound_block"])
    t0 = _time.monotonic()
    ins_ = {"calls": 0, "splits": {"result_count": 0, "span": 0, "other": 0}, "max_ok_span": 0}   # dry-run report inputs (V1-3/V1-4)
    first = {}                                    # Y -> (first_block, first_ts)
    subs, st = [], {"capped": False, "cap_block": None, "cap_ts": None, "failed": False}

    def get(lo, hi, topics):
        ins_["calls"] += 1
        return cl.rpc_call("eth_getLogs", [{"address": TOKENS, "fromBlock": hex(lo), "toBlock": hex(hi), "topics": topics}])

    def leaf(lo, hi, depth):
        if st["capped"] or st["failed"]:
            return
        outs, e1 = get(lo, hi, [TRANSFER, _pad(X)])
        ins, e2 = (None, None) if e1 is not None else get(lo, hi, [TRANSFER, None, _pad(X)])
        err = e1 if e1 is not None else e2
        if err is not None:
            if any(t in str(err).lower() for t in TOO_BIG) and depth < 24 and hi > lo:
                m = str(err).lower()
                ins_["splits"]["result_count" if ("result" in m or "more than" in m) else "span" if ("range" in m or "block" in m) else "other"] += 1
                mid = (lo + hi) // 2
                leaf(lo, mid, depth + 1)          # ascending: the lower half is finished before the upper half starts
                leaf(mid + 1, hi, depth + 1)
                return
            subs.append({"from": lo, "to": hi, "status": "unavailable", "error": str(err)[:200]})
            st["failed"] = True
            return
        ins_["max_ok_span"] = max(ins_["max_ok_span"], hi - lo + 1)
        merged = {}
        for lg in list(outs) + list(ins):          # a self-transfer appears in both streams: one log, one key
            merged[_key(lg)] = lg
        for k in sorted(merged):                   # the single global order (block, log_index)
            lg = merged[k]
            frm, to = _addr(lg["topics"][1]), _addr(lg["topics"][2])
            y = to if frm == X else frm
            if y == X or y in first:
                continue
            ts = int(lg["blockTimestamp"], 16) if lg.get("blockTimestamp") else None
            first[y] = (k[0], ts)
            if len(first) >= cap:
                st.update(capped=True, cap_block=k[0], cap_ts=ts)
                subs.append({"from": lo, "to": hi, "status": "ok", "n": len(merged), "capped_here": True})
                return
        subs.append({"from": lo, "to": hi, "status": "ok", "n": len(merged)})

    leaf(lo0, int(to_block), 0)
    return {"address": X, "status": "unavailable" if st["failed"] else "ok", "capped": st["capped"], "cap_block": st["cap_block"],
            "cap_ts": st["cap_ts"], "cap": int(cap), "n_distinct": len(first),
            "first_blocks": {y: v[0] for y, v in sorted(first.items())}, "first_ts": {y: v[1] for y, v in sorted(first.items())},
            "from_block": lo0, "to_block": int(to_block), "subranges": subs, "fetched_at": utcnow(),
            "calls": ins_["calls"], "splits": ins_["splits"], "max_ok_span": ins_["max_ok_span"], "elapsed_s": round(_time.monotonic() - t0, 3)}


def block_before(cl, t_unix, head=None):
    """block(t) = the last block with timestamp < t (strict; R16A-7). Binary search over eth_getBlockByNumber."""
    def ts(n):
        blk, err = cl.rpc_call("eth_getBlockByNumber", [hex(n), False])
        if err is not None:
            raise RuntimeError(f"block {n}: {err}")
        return int(blk["timestamp"], 16)
    if head is None:
        h, err = cl.rpc_call("eth_blockNumber", [])
        if err is not None:
            raise RuntimeError(f"head: {err}")
        head = int(h, 16)
    lo, hi = 0, int(head)
    if ts(lo) >= t_unix:
        raise RuntimeError("t precedes the genesis block timestamp")
    if ts(hi) < t_unix:
        return hi
    while hi - lo > 1:                       # invariant: ts(lo) < t <= ts(hi)
        mid = (lo + hi) // 2
        if ts(mid) < t_unix:
            lo = mid
        else:
            hi = mid
    return lo


def lookup_set(edges, natural, stop):
    """s6_lookup_set: hop-1 counterparties (not stop-listed) shared by >= 2 profiled wallets of which >= 1 is natural.
    edges: iterable of (wallet, counterparty) hop-1 pairs (any direction). Returns {X: (n_sharers, n_natural_sharers)}."""
    by = {}
    for w, x in edges:
        x = x.lower()
        if x in stop:
            continue
        by.setdefault(x, set()).add(w.lower())
    out = {}
    for x, ws in by.items():
        nn = sum(1 for w in ws if w in natural)
        if len(ws) >= 2 and nn >= 1:
            out[x] = (len(ws), nn)
    return out


def run_lookups(cl, prof_dir: Path, lset: dict, to_block: int, cap: int, writer_id: str, stop_flag=None):
    """Resumable: lookups/<X>.json (atomic .part -> replace), each carrying writer_id. Returns counts."""
    d = prof_dir / "lookups"
    d.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for x in sorted(lset):
        if stop_flag is not None and stop_flag.is_set():
            break
        target = d / f"{x}.json"
        if target.exists():
            continue
        rec = lookup_activity(cl, x, to_block, cap)
        rec.update(writer_id=writer_id, n_sharers=lset[x][0], n_natural_sharers=lset[x][1])
        part = target.with_suffix(".json.part")
        part.write_bytes(json.dumps(rec, sort_keys=True).encode())
        part.replace(target)
        n_new += 1
    return {"lookup_set": len(lset), "fetched_now": n_new}
