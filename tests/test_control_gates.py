"""Control gates must abort on the failure they guard, and must not abort on healthy data.
All HTTP is served by httpx.MockTransport - no network."""
import json
import pathlib

import duckdb
import httpx
import pytest

from radar.http import Fetcher
from radar.ingest import ControlGateError, check_trades_gate, run_ingest

COND = "0x" + "ab" * 32
TOK_Y, TOK_N = "111", "222"


def trade(i, wallet="0x" + "11" * 20):
    return {"proxy_wallet": wallet, "side": "BUY", "token_id": TOK_Y, "condition_id": COND, "size": 10.0,
            "price": 0.2, "timestamp": 1772271000 + i, "transaction_hash": f"0x{i:064x}",
            "name": "should-not-be-stored", "pseudonym": "x", "bio": "", "profile_image": ""}


def page(rows, cursor=None):
    return {"data": rows, "pagination": {"limit": 1000, "offset": 0, "has_more": bool(cursor), "next_cursor": cursor}}


def make_transport(taker_rows, all_rows, gamma_volume="20.0", gamma_empty=False):
    def handler(req: httpx.Request):
        u, q = req.url, req.url.params
        if u.host == "gamma-api.polymarket.com" and gamma_empty:
            return httpx.Response(200, json=[])  # what Gamma returns for a closed market without closed=true
        if u.host == "gamma-api.polymarket.com":
            return httpx.Response(200, json=[{
                "id": "1", "conditionId": COND, "question": "q?", "negRisk": False, "volume": gamma_volume,
                "clobTokenIds": json.dumps([TOK_Y, TOK_N]), "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]',
                "closedTime": "2026-02-28 09:31:17+00", "createdAt": "2026-01-16T14:49:30Z",
                "acceptingOrdersTimestamp": "2026-01-19T20:34:10Z", "events": [{"id": "9"}]}])
        if u.path == "/v2/trades":
            return httpx.Response(200, json=page(all_rows if q.get("taker_only") == "false" else taker_rows))
        if u.path == "/v2/holders":
            return httpx.Response(200, json=page([{"token_id": TOK_Y, "holders": [
                {"proxy_wallet": "0x" + "11" * 20, "token_id": TOK_Y, "amount": 20.0, "outcome_index": 0,
                 "name": "should-not-be-stored"}]}]))
        if u.path == "/v2/positions":
            return httpx.Response(200, json=page([{"proxy_wallet": "0x" + "11" * 20, "token_id": TOK_Y,
                                                   "condition_id": COND, "status": "REDEEMABLE"}]))
        if u.path == "/v2/resolutions":
            return httpx.Response(200, json=page([{"condition_id": COND, "question_id": "0x01", "status": "resolved",
                                                   "transaction_hash": "0xcreate", "last_update_timestamp": "1772271077"}]))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def ingest(tmp_path, taker_rows, all_rows, **kw):
    return run_ingest([COND], fetcher=Fetcher(transport=make_transport(taker_rows, all_rows, **kw), sleep=lambda s: None),
                      snap_root=tmp_path / "snaps")


def test_healthy_market_loads(tmp_path):
    taker = [trade(0), trade(1)]
    out = ingest(tmp_path, taker, taker + [trade(0, "0x" + "22" * 20), trade(1, "0x" + "22" * 20)])
    assert out["status"] == "ok", out
    r = out["results"][COND]
    assert r["status"] == "loaded"
    assert r["checks"]["U-UNITS"]["ratio"] == pytest.approx(1.0)
    con = duckdb.connect(out["db"])
    assert con.execute("SELECT walk, count(*) FROM trades GROUP BY 1 ORDER BY 1").fetchall() == [("all", 4), ("taker", 2)]
    # identity fields never reach derived tables
    from radar.config import IDENTITY_FIELDS
    cols = {c for (c,) in con.execute("SELECT column_name FROM information_schema.columns").fetchall()}
    assert cols and not cols & IDENTITY_FIELDS


def test_empty_trades_page_aborts(tmp_path):
    """Design note §3.2 positive control: 0 trade rows while Gamma volume > 0 -> abort, load nothing."""
    out = ingest(tmp_path, [], [])
    assert out["status"] == "aborted", out
    assert out["results"][COND]["reason"].startswith("G-TRADES")
    con = duckdb.connect(out["db"])
    assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM markets").fetchone()[0] == 0


def test_ignored_taker_only_param_aborts(tmp_path):
    """A2b: if taker_only=false is silently ignored, both walks match -> abort."""
    taker = [trade(0), trade(1)]
    out = ingest(tmp_path, taker, list(taker))
    assert out["status"] == "aborted"
    assert out["results"][COND]["reason"].startswith("G-A2B")


def test_gate_unit():
    with pytest.raises(ControlGateError):
        check_trades_gate(100.0, 0, 0)
    with pytest.raises(ControlGateError):
        check_trades_gate(100.0, 5, 5)
    check_trades_gate(100.0, 5, 12)          # healthy
    with pytest.raises(ControlGateError):
        check_trades_gate(0.0, 0, 0)         # zero-volume market with no trades still trips A2b (0 == 0)


def test_identical_pages_both_walks_load_without_key_clash(tmp_path):
    """Regression (found when A2b gate was disabled): byte-identical taker/all pages share snapshot_id.
    Loading them must not collide; the gate is bypassed here to reach the loader."""
    import radar.ingest as ing
    orig = ing.check_trades_gate
    ing.check_trades_gate = lambda *a: None
    try:
        taker = [trade(0), trade(1)]
        out = ingest(tmp_path, taker, list(taker))
    finally:
        ing.check_trades_gate = orig
    assert out["status"] == "ok", out
    con = duckdb.connect(out["db"])
    assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 4
    assert con.execute("SELECT count(DISTINCT snapshot_id), count(*) FROM snapshots WHERE request_name LIKE 'trades_%'").fetchone() == (1, 2)


def test_gamma_lookup_empty_aborts(tmp_path):
    """G-MARKET: Gamma returning no market for the condition must abort, not load an empty market."""
    taker = [trade(0)]
    out = ingest(tmp_path, taker, taker + [trade(0, "0x" + "22" * 20)], gamma_empty=True)
    assert out["status"] == "aborted", out
    assert out["results"][COND]["reason"].startswith("G-MARKET")


def test_aborted_market_fetches_are_in_manifest(tmp_path):
    """Provenance: pages fetched for an aborted market are listed in the manifest (raw on disk == listed)."""
    out = ingest(tmp_path, [], [])
    man = json.loads(open(out["manifest"], "rb").read())
    assert out["status"] == "aborted"
    assert man["raw_on_disk_not_listed"] == [] and man["listed_not_on_disk"] == []
    assert any(f["name"].startswith("trades_taker_") for f in man["files"])


def test_unlisted_raw_file_flags_provenance_mismatch(tmp_path):
    """A raw file on disk that the snapshots table does not list must flip status (both-direction check)."""
    import radar.ingest as ing
    taker = [trade(0)]
    out = ingest(tmp_path, taker, taker + [trade(0, "0x" + "22" * 20)])
    snap_dir = pathlib.Path(out["manifest"]).parent
    (snap_dir / "raw" / "stray.json").write_bytes(b"{}")
    con = ing.connect(snap_dir / "radar.duckdb")
    res = ing.finalize(con, snap_dir, {})
    con.close()
    assert res["status"] == "provenance_mismatch"


def test_resume_finalize_rebuilds_manifest(tmp_path):
    """SNAP whose manifest never got written (export crash): resume_finalize rebuilds it from the DB alone."""
    import radar.ingest as ing
    taker = [trade(0), trade(1)]
    out = ingest(tmp_path, taker, taker + [trade(0, "0x" + "22" * 20)])
    snap_dir = pathlib.Path(out["manifest"]).parent
    first = json.loads(open(out["manifest"], "rb").read())
    (snap_dir / "manifest.json").unlink()
    con = duckdb.connect(out["db"]); con.execute("DELETE FROM ingest_results"); con.close()   # simulate pre-table run
    res = ing.resume_finalize(snap_dir, {})
    again = json.loads(open(res["manifest"], "rb").read())
    assert res["status"] == "ok"
    assert [f["snapshot_id"] for f in again["files"]] == [f["snapshot_id"] for f in first["files"]]
    assert again["results"][COND]["checks"] == first["results"][COND]["checks"]


def test_duckdb_spill_and_memory_cap_applied(tmp_path):
    import radar.ingest as ing
    from radar import config
    con = ing.connect(tmp_path / "x.duckdb")
    tmp = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
    lim = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    con.close()
    assert pathlib.Path(tmp).resolve() == config.TMP_DIR.resolve()
    assert lim == "1.8 GiB"  # DuckDB reports 2GB (decimal) as 1.8 GiB
