"""Profile fetcher (targeted calls, shapes, transfers, bisect) and r7 event anchors. No network."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from radar import events, profile

WAL = "0x" + "ab" * 20
FUNDER, DEPOSIT = "0x" + "f1" * 20, "0x" + "d1" * 20
EXCH = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
P = {"s6_max_hops": 2, "s6_hop_breadth": 2}


def log(frm, to, amt, ts, i=1):
    return {"address": profile.TOKENS[0], "topics": [profile.TRANSFER, "0x" + "0" * 24 + frm[2:], "0x" + "0" * 24 + to[2:]],
            "data": hex(int(amt * 1e6)), "blockTimestamp": hex(ts), "transactionHash": f"0x{i:064x}", "logIndex": hex(i)}


def api_handler(asc_ts=1000, desc_ts=2000, redeem=True):
    def h(req):
        q = dict(req.url.params)
        if req.url.path == "/v2/user-stats":
            return httpx.Response(200, json={"data": {"all_time_pnl": {"volume_usdc": 12345.0}}})
        if q.get("type") == "REDEEM":
            return httpx.Response(200, json={"data": [{"timestamp": 5000, "transaction_hash": "0xred"}] if redeem else []})
        if q.get("type") == "TRADE" and q.get("start"):
            return httpx.Response(200, json={"data": []})
        if q.get("sort_direction") == "ASC":
            return httpx.Response(200, json={"data": [{"timestamp": asc_ts}]})
        return httpx.Response(200, json={"data": [{"timestamp": desc_ts}]})
    return httpx.MockTransport(h)


def rpc_handler(tx_to=WAL, sel="0x6a761202", too_many_first=False):
    state = {"n": 0}

    def h(req):
        b = json.loads(req.content)
        m, p = b["method"], b["params"]
        if m == "eth_getTransactionByHash":
            return httpx.Response(200, json={"result": {"to": tx_to, "from": "0x" + "99" * 20, "input": sel + "00"}})
        if m == "eth_blockNumber":
            return httpx.Response(200, json={"result": hex(1000)})
        if m == "eth_getLogs":
            state["n"] += 1
            f = p[0]
            if too_many_first and f["fromBlock"] == "0x0" and f["toBlock"] == "latest":
                return httpx.Response(200, json={"error": {"message": "query returned more than 20000 results"}})
            topics = f["topics"]
            inbound = len(topics) == 3
            addr = "0x" + topics[-1][-40:]
            if addr == WAL and inbound:
                return httpx.Response(200, json={"result": [log(FUNDER, WAL, 500, 900, 1), log(EXCH, WAL, 50, 800, 2)]})
            if addr == WAL and not inbound:
                return httpx.Response(200, json={"result": [log(WAL, DEPOSIT, 400, 3000, 3), log(WAL, EXCH, 60, 1500, 4)]})
            return httpx.Response(200, json={"result": []})
        return httpx.Response(400)
    return httpx.MockTransport(h), state


def test_profile_wallet_rows():
    rt, _ = rpc_handler()
    cl = profile.Clients(api_transport=api_handler(), rpc_transport=rt, sleep=lambda s: None)
    b = profile.profile_wallet(cl, WAL, ["c1"], stop={EXCH}, P=P)
    prof, redeems, after, transfers = profile.bundle_to_rows(b, infra={EXCH})
    assert prof[1] == 1000 and prof[3] == 12345.0            # first_trade_ts (ASC), lifetime volume
    assert prof[2] == 900                                     # first funding: FUNDER at 900, exchange flow (800) excluded
    assert redeems == [(WAL, "c1", 5000, "safe_exec", "0xred")] and after == [(WAL, "c1", None)]
    cps = {(d, cp) for (_, d, hop, cp, *_r) in transfers if hop == 1}
    assert ("in", FUNDER) in cps and ("out", DEPOSIT) in cps


def test_sort_asc_not_honoured_aborts():
    rt, _ = rpc_handler()
    cl = profile.Clients(api_transport=api_handler(asc_ts=3000, desc_ts=2000), rpc_transport=rt, sleep=lambda s: None)
    with pytest.raises(profile.ProfileError):
        profile.profile_wallet(cl, WAL, [], stop=set(), P=P)


def test_shapes():
    assert profile.classify_shape({"to": WAL, "input": "0x6a761202ab"}, WAL) == "safe_exec"
    assert profile.classify_shape({"to": profile.RELAY_HUB, "input": "0x405cec67ab"}, WAL) == "relay_hub"
    assert profile.classify_shape({"to": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045", "from": WAL, "input": "0x01b7037c"}, WAL) == "direct_eoa"
    assert profile.classify_shape({"to": profile.ERC4337_ENTRYPOINT, "from": "0x1", "input": "0x765e827f"}, WAL) == "erc4337"
    assert profile.classify_shape({"to": "0x2", "from": "0x3", "input": "0xdeadbeef"}, WAL) == "other"


def test_get_logs_bisects_on_too_many_results():
    rt, state = rpc_handler(too_many_first=True)
    cl = profile.Clients(rpc_transport=rt, sleep=lambda s: None)
    logs, st = profile.get_logs(cl, WAL, "in")
    assert st == "ok" and state["n"] >= 3 and len(logs) >= 2


# ---------------------------------------------------------------- r7 anchors
HDR = "condition_id,event_ts_utc,event_ts_precision,event_tz,hedged_report_ts_utc,hedged_report_ts_precision,hedged_report_tz,tier_gap,occurrence_ts_precision\n"


def test_r7_anchor_day_unknown_tz_is_coarse(tmp_path):
    f = tmp_path / "ev.csv"
    f.write_text(HDR + "c1,2026-02-28T00:00:00Z,day,,,,,false,day\n")
    (c, anchor, prec, na), = events.load_anchors(f, 24)[0]
    assert anchor == datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc)   # 2026-02-28 00:00 at UTC+14
    assert prec == 86400 and na == "event_ts_too_coarse"


def test_r7_anchor_min_of_terms_and_tier_gap(tmp_path):
    f = tmp_path / "ev.csv"
    f.write_text(HDR + "c2,2026-02-28T06:15:00Z,minute,UTC,2026-02-28T05:20:00Z,hour,UTC,false,minute\n"
                       "c3,2026-02-28T06:15:00Z,minute,UTC,,,,true,day\n"
                       "c4,2026-02-28T06:15:00Z,minute,UTC,,,,true,hour\n")
    rows = {r[0]: r for r in events.load_anchors(f, 24)[0]}
    assert rows["c2"][1] == datetime(2026, 2, 28, 5, 0, tzinfo=timezone.utc) and rows["c2"][2] == 3600 and rows["c2"][3] is None
    assert rows["c3"][3] == "event_ts_too_coarse" and rows["c4"][3] is None


def test_r7_missing_column_refuses(tmp_path):
    f = tmp_path / "ev.csv"
    f.write_text("condition_id,event_ts_utc\nc1,2026-02-28T00:00:00Z\n")
    with pytest.raises(events.EventFileError):
        events.load_anchors(f, 24)


def test_profile_truncation_flag():
    rt, _ = rpc_handler()
    cl = profile.Clients(api_transport=api_handler(), rpc_transport=rt, sleep=lambda s: None)
    b = profile.profile_wallet(cl, WAL, [], stop={EXCH}, P=P)
    for e in b["transfers"]["in"]["edges"]:
        e["first_block"] = 100              # funding within 1M blocks of the genesis bound
    prof = profile.bundle_to_rows(b, infra={EXCH})[0]
    assert prof[9] == 100 and prof[10] is True
    for e in b["transfers"]["in"]["edges"]:
        e["first_block"] = 5_000_000        # well after the bound
    assert profile.bundle_to_rows(b, infra={EXCH})[0][10] is False
    b["transfers"]["in"]["edges"] = [e for e in b["transfers"]["in"]["edges"] if e["counterparty"] == EXCH]
    assert profile.bundle_to_rows(b, infra={EXCH})[0][10] is True   # no non-infra inbound but trades exist
