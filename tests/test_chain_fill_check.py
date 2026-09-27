"""tools/chain_fill_check.py exchange selection (NegRisk calibration + wrong-exchange negative control, 2026-09-26)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("cfc", Path(__file__).resolve().parent.parent / "tools" / "chain_fill_check.py")
cfc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfc)


def test_exchange_by_neg_risk_and_override():
    assert cfc.select_exchange(False) == "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"   # Polymarket: CTF Exchange
    assert cfc.select_exchange(True) == "0xc5d563a36ae78145c45a50134d48a1215220f80a"    # Polymarket: Neg Risk CTF Exchange
    assert cfc.select_exchange(True, "0x4BFB41D5B3570DEFD03C39A9A4D8DE6BD8B8982E") == "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"


# ---- QA 2026-09-27: missing-fill DETECTION (the comparison), with the pinned tool unedited: only rpc + SNAP are mocked
import json
import sys

import pyarrow as pa
import pyarrow.parquet as pq

B0, T0 = 80_000_000, 1_770_000_000          # block B0 has timestamp T0; 2 s per block
COND, TOK = "0x" + "c" * 64, "123456789"


def _word(x):
    return f"{x:064x}"


def _log(block, tx, size_shares):
    # OrderFilled data words: makerAssetId=0 (USDC) -> BUY of takerAssetId; takerAmountFilled = shares * 1e6
    data = "0x" + _word(0) + _word(int(TOK)) + _word(1_000_000) + _word(int(size_shares * 1e6)) + _word(0)
    return {"data": data, "blockNumber": hex(block), "transactionHash": tx}


def _run(tmp_path, monkeypatch, chain, api):
    """chain: [(block, tx, size)] planted as OrderFilled logs; api: [(block, tx, size)] planted as API taker rows."""
    snap = tmp_path / "snap"; snap.mkdir()
    pq.write_table(pa.table({"condition_id": [COND], "neg_risk": [False], "accepting_orders_at": pa.array([None], pa.timestamp("us", tz="UTC")),
                             "created_at": pa.array([T0 - 86400], pa.int64()).cast(pa.timestamp("s", tz="UTC")),
                             "closed_at": pa.array([T0 + 86400], pa.int64()).cast(pa.timestamp("s", tz="UTC"))}), snap / "markets.parquet")
    pq.write_table(pa.table({"token_id": [TOK], "condition_id": [COND]}), snap / "tokens.parquet")
    pq.write_table(pa.table({"walk": ["taker"] * len(api), "condition_id": [COND] * len(api),
                             "ts": pa.array([T0 + 2 * (b - B0) for b, _, _ in api], pa.int64()).cast(pa.timestamp("s", tz="UTC")),
                             "tx_hash": [tx for _, tx, _ in api], "token_id": [TOK] * len(api), "side": ["BUY"] * len(api),
                             "size": [float(s) for _, _, s in api]}), snap / "trades.parquet")
    logs = [_log(b, tx, s) for b, tx, s in chain]

    def fake_rpc(method, params):
        if method == "eth_blockNumber":
            return hex(B0 + 100_000)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(T0 + 2 * (int(params[0], 16) - B0))}
        if method == "eth_getLogs":
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            return [lg for lg in logs if lo <= int(lg["blockNumber"], 16) <= hi]
        raise AssertionError(method)
    monkeypatch.setattr(cfc, "rpc", fake_rpc)
    monkeypatch.setattr(cfc, "SNAP", snap.as_posix())
    cfc._ts.clear(); cfc._anchor.clear()
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["chain_fill_check.py", COND, "--k-centred", "1", "--k-uniform", "0", "--w", "50", "--out", str(out)])
    cfc.main()
    return json.loads(out.read_bytes())["totals"]


FILLS = [(B0 + 10, "0x" + "a" * 64, 5.0), (B0 + 12, "0x" + "b" * 64, 7.25), (B0 + 14, "0x" + "d" * 64, 1.5)]


def test_detection_identical_sets_zero(tmp_path, monkeypatch):
    t = _run(tmp_path, monkeypatch, FILLS, FILLS)
    assert t["chain_fills"] == 3 and t["api_rows"] == 3
    assert t["on_chain_not_api"] == 0 and t["in_api_not_chain"] == 0


def test_detection_api_row_removed(tmp_path, monkeypatch):
    t = _run(tmp_path, monkeypatch, FILLS, FILLS[:1] + FILLS[2:])
    assert t["on_chain_not_api"] == 1 and t["in_api_not_chain"] == 0


def test_detection_extra_api_row(tmp_path, monkeypatch):
    t = _run(tmp_path, monkeypatch, FILLS, FILLS + [(B0 + 13, "0x" + "e" * 64, 2.0)])
    assert t["on_chain_not_api"] == 0 and t["in_api_not_chain"] == 1
