"""P&L (design note r9 §2): trades-derived, all timestamps, void excluded, display only."""
import duckdb
from pathlib import Path

import pyarrow  # noqa: F401
from radar import score
from test_g4_score import H, RES, W, make_snap, world, PARAMS


def run_with(tmp_path, trades, void=()):
    snap = make_snap(tmp_path / "s", trades, void=void)
    _, wf, lock = world(tmp_path / "w")
    pf = tmp_path / "p.toml"
    pf.write_text(PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa"]\n')
    out = score.run(snap, wf, pf, lock=lock, runs_dir=tmp_path / "runs")
    return Path(out["dir"])


def q(d, sql, *a):
    return duckdb.connect().execute(sql.replace("{d}", d.as_posix()), list(a)).fetchall()


def test_planted_pnl_65_and_post_resolution_sell(tmp_path):
    tr = [("m1", 0, W(1), "BUY", 100, 0.2, RES - 10 * H), ("m1", 0, W(1), "SELL", 30, 0.5, RES - 5 * H)]
    d = run_with(tmp_path / "a", tr)
    assert q(d, "SELECT pnl_trades FROM '{d}/pnl_market.parquet' WHERE proxy_wallet = ?", W(1)) == [(65.0,)]
    tr2 = tr + [("m1", 0, W(1), "SELL", 10, 0.99, RES + H)]           # after resolution: still a cash flow
    d2 = run_with(tmp_path / "b", tr2)
    (v,), = q(d2, "SELECT pnl_trades FROM '{d}/pnl_market.parquet' WHERE proxy_wallet = ?", W(1))
    assert abs(v - (15 + 9.9 - 20 + 60)) < 1e-9


def test_void_market_excluded(tmp_path):
    tr = [("m1", 0, W(1), "BUY", 100, 0.2, RES - 10 * H), ("m2", 0, W(1), "BUY", 50, 0.5, RES - 10 * H)]
    d = run_with(tmp_path, tr, void=("m2",))
    assert q(d, "SELECT pnl_trades FROM '{d}/pnl_market.parquet' WHERE condition_id = 'm2'") == [(None,)]
    assert q(d, "SELECT n_markets, n_void_excluded, pnl_trades FROM '{d}/pnl.parquet' WHERE scope = 'all' AND proxy_wallet = ?", W(1)) == [(1, 1, 80.0)]
