"""Step-4 side-car and the scoring-view refuse check (QA A10). No network: RPC served by httpx.MockTransport."""
import json
from datetime import datetime, timezone

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from radar.scoring_view import ResolutionMissing, build_scoring_views
from radar.step4 import CR, Rpc, Step4Error, run_step4

C1 = "0x" + "11" * 32   # resolves Yes
C2 = "0x" + "22" * 32   # void (50/50)
RTS = {C1: 1772271077, C2: 1772761567}
BLK = {C1: 83575523, C2: 83820768}


def ts(u):
    return datetime.fromtimestamp(u, tz=timezone.utc)


def make_snap(tmp_path, conds=(C1, C2)):
    snap = tmp_path / "SNAP-T"
    (snap / "derived").mkdir(parents=True)
    (snap / "manifest.json").write_bytes(b'{"snap":"SNAP-T"}')
    pq.write_table(pa.table({
        "condition_id": list(conds),
        "closed_at": [ts(RTS[c]) for c in conds],
        "resolved_outcome_index": pa.array([0 if c == C1 else None for c in conds], pa.int64()),
    }), snap / "derived" / "markets.parquet")
    rows = []
    for c in conds:
        r = RTS[c]
        rows += [(c, "taker", r - 100), (c, "taker", r - 1), (c, "taker", r), (c, "taker", r + 50), (c, "all", r - 1)]
    pq.write_table(pa.table({"condition_id": [x[0] for x in rows], "walk": [x[1] for x in rows],
                             "ts": [ts(x[2]) for x in rows], "size": [1.0] * len(rows)}),
                   snap / "derived" / "trades.parquet")
    return snap


def payout_data(payouts):
    words = [len(payouts), 64, len(payouts)] + payouts
    return "0x" + "".join(f"{w:064x}" for w in words)


def transport(n_logs=None):
    """n_logs: dict cond -> number of logs to return (default 1)."""
    n_logs = n_logs or {}

    def handler(req):
        body = json.loads(req.content)
        m, p = body["method"], body["params"]
        if m == "eth_getLogs":
            cond = p[0]["topics"][1]
            assert p[0]["topics"][0] == CR
            one = {"blockNumber": hex(BLK[cond]), "transactionHash": "0x" + cond[2:6] * 16, "logIndex": "0x1",
                   "topics": [CR, cond, "0x" + "0" * 24 + "65070be91477460d8a7aeeb94ef92fe056c2f2a7"],
                   "data": payout_data([1, 0] if cond == C1 else [1, 1])}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [one] * n_logs.get(cond, 1)})
        if m == "eth_getBlockByNumber":
            b = int(p[0], 16)
            cond = C1 if b == BLK[C1] else C2
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"timestamp": hex(RTS[cond])}})
        return httpx.Response(400)
    return httpx.MockTransport(handler)


def test_step4_resolves_and_computes_t_ref(tmp_path):
    snap = make_snap(tmp_path)
    out = run_step4(snap, rpc=Rpc(transport=transport()))
    assert out["status"] == "ok" and out["rows"] == 2
    t = pq.read_table(snap / "step4" / "resolutions_chain.parquet").to_pylist()
    r = {x["condition_id"]: x for x in t}
    assert r[C1]["resolution_ts_unix"] == RTS[C1] and r[C1]["chain_winner_index"] == 0 and not r[C1]["void"]
    assert r[C2]["void"] and r[C2]["chain_winner_index"] is None
    # t_ref = last TAKER fill strictly before resolution: r-1 (not r, not r+50, not the 'all' row)
    assert r[C1]["t_ref_unix"] == RTS[C1] - 1
    assert out["results"][C1]["checks"]["U-PAYOUT"]["passed"] and out["results"][C2]["checks"]["U-PAYOUT"]["passed"]
    man = json.loads((snap / "step4" / "manifest.json").read_bytes())
    assert man["snap_manifest_sha256"] and len(man["files"]) == 4


def test_step4_zero_or_two_logs_aborts_condition(tmp_path):
    snap = make_snap(tmp_path)
    out = run_step4(snap, rpc=Rpc(transport=transport({C1: 0, C2: 2})))
    assert out["status"] == "incomplete" and out["rows"] == 0
    assert out["results"][C1]["reason"].startswith("S4-ONE") and out["results"][C2]["reason"].startswith("S4-ONE")


def test_step4_never_overwrites(tmp_path):
    snap = make_snap(tmp_path)
    run_step4(snap, rpc=Rpc(transport=transport()))
    with pytest.raises(Step4Error):
        run_step4(snap, rpc=Rpc(transport=transport()))


def test_scoring_view_refuses_without_side_car(tmp_path):
    snap = make_snap(tmp_path)
    with pytest.raises(ResolutionMissing):
        build_scoring_views(duckdb.connect(), snap)


def test_scoring_view_refuses_when_a_condition_lacks_resolution(tmp_path):
    snap = make_snap(tmp_path)
    run_step4(snap, rpc=Rpc(transport=transport({C2: 0})))   # C2 aborted -> no row
    with pytest.raises(ResolutionMissing):
        build_scoring_views(duckdb.connect(), snap)
    build_scoring_views(duckdb.connect(), snap, scope_conditions=[C1])   # scope without C2 is fine


def test_signal_fills_strictly_before_resolution(tmp_path):
    snap = make_snap(tmp_path)
    run_step4(snap, rpc=Rpc(transport=transport()))
    con = duckdb.connect()
    build_scoring_views(con, snap)
    got = sorted(con.execute("SELECT condition_id, walk, epoch(ts)::BIGINT FROM signal_fills WHERE condition_id = ?", [C1]).fetchall())
    # kept: r-100, r-1 (taker), r-1 (all); dropped: r (same second) and r+50 (post-resolution)
    assert got == sorted([(C1, "taker", RTS[C1] - 100), (C1, "taker", RTS[C1] - 1), (C1, "all", RTS[C1] - 1)])
