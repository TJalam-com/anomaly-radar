"""Scorer run tests (r6 Δ3 A2/A3, Δ4 A4, Δ5 modes, r5 Δ4 isolation, r4 §5 weights lock). Synthetic on-disk SNAP."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from radar import score

RES = 1_772_000_000
H = 3600
PARAMS = """[params]
p_low = 0.20
pre_min_stake = 1000
s1_min_stake = 1000
s1_fresh_h = 48
s1_stale_h = 720
s3_min_stake = 1000
s3_cap_usdc = 100000
s4_lead_h = 24
s4_min_stake = 1000
s4_cap_usdc = 50000
s5_full_at = 6.0
s5_min_bets = 3
s6_max_hops = 3
s6_full_at = 5
s6_fanout_max = 20
s6_hop_breadth = 3
s7_min_stake = 1000
s7_window_s = 600
s7_full_at = 5
s8_quick_h = 24
s8_dormant_d = 30
"""
WEIGHTS = "version = 'test'\n[weights]\n" + "".join(f"S{i} = 1.0\n" for i in range(1, 9))
PARAMS_FILE = None


def T(u):
    return datetime.fromtimestamp(u, tz=timezone.utc)


def W(n):
    return "0x" + f"{n:040x}"


def make_snap(root: Path, trades, void=()):
    """trades: (cond, outcome_idx, wallet, side, size, price, ts). Two markets, both resolve outcome 0 at RES."""
    snap = root / "SNAP-X"
    (snap / "derived").mkdir(parents=True)
    (snap / "step4").mkdir()
    conds = sorted({t[0] for t in trades})
    pq.write_table(pa.table({"condition_id": conds, "event_id": ["e1"] * len(conds),
                             "resolved_outcome_index": pa.array([0] * len(conds), pa.int64())}), snap / "derived" / "markets.parquet")
    toks = [(f"{c}-t{i}", c, i) for c in conds for i in (0, 1)]
    pq.write_table(pa.table({"token_id": [t[0] for t in toks], "condition_id": [t[1] for t in toks],
                             "outcome_index": [t[2] for t in toks]}), snap / "derived" / "tokens.parquet")
    rows = []
    for i, tr in enumerate(trades):
        c, oi, w, side, size, price, ts = tr[:7]
        for walk in (tr[7] if len(tr) > 7 else ("all", "taker")):
            rows.append(dict(snapshot_id=f"p{walk}", seq=i, walk=walk, tx_hash=f"0x{i:064x}", condition_id=c,
                             token_id=f"{c}-t{oi}", proxy_wallet=w, side=side, size=float(size), price=float(price), ts=T(ts)))
    pq.write_table(pa.Table.from_pylist(rows), snap / "derived" / "trades.parquet")
    pq.write_table(pa.table({"condition_id": conds, "resolution_tx": ["0x1"] * len(conds),
                             "resolution_log_index": pa.array([1] * len(conds), pa.int64()),
                             "resolution_block": pa.array([1] * len(conds), pa.int64()),
                             "resolution_ts_unix": pa.array([RES] * len(conds), pa.int64()), "oracle": ["0x0"] * len(conds),
                             "payouts_json": ["[1, 1]" if c in void else "[1, 0]" for c in conds], "void": [c in void for c in conds],
                             "chain_winner_index": pa.array([None if c in void else 0 for c in conds], pa.int64()),
                             "t_ref_unix": pa.array([RES - 1] * len(conds), pa.int64()),
                             "closed_at_delta_s": pa.array([0] * len(conds), pa.int64())}),
                   snap / "step4" / "resolutions_chain.parquet")
    (snap / "manifest.json").write_bytes(b"{}")
    (snap / "step4" / "manifest.json").write_bytes(b"{}")
    return snap


def world(tmp_path):
    tr = []
    for i in range(4):   # coordinated low-odds cluster in m1
        tr.append(("m1", 0, W(10 + i), "BUY", 2000 / 0.12, 0.12, RES - 10 * H + i * 60))
    tr.append(("m1", 1, W(30), "BUY", 50, 0.9, RES - 5 * H))         # ordinary wallet
    tr.append(("m2", 0, W(31), "BUY", 10, 0.5, RES - 20 * H))        # wallet only in m2
    tr.append(("m2", 0, W(10), "BUY", 100, 0.5, RES - 30 * H))
    for i in range(6):   # background low-odds buys spread out in time
        tr.append(("m2", 1, W(40 + i), "BUY", 1500 / 0.1, 0.1, RES - 200 * H + i * 7000))
    snap = make_snap(tmp_path, tr)
    wf = tmp_path / "weights.toml"
    wf.write_text(WEIGHTS)
    lock = tmp_path / "weights.toml.lock"
    lock.write_text(hashlib.sha256(wf.read_bytes()).hexdigest())
    pf = tmp_path / "params.toml"
    pf.write_text(PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa"]\n')
    global PARAMS_FILE
    PARAMS_FILE = pf
    return snap, wf, lock


def independent_U(snap):
    return duckdb.connect().execute(f"SELECT count(DISTINCT proxy_wallet) FROM '{(snap / 'derived' / 'trades.parquet').as_posix()}' WHERE walk = 'all'").fetchone()[0]


def load(run_dir, name):
    return duckdb.connect().execute(f"SELECT * FROM '{(Path(run_dir) / (name + '.parquet')).as_posix()}'").fetchall()


def test_dense_scores_and_signals(tmp_path):
    snap, wf, lock = world(tmp_path)
    out = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs")
    d = Path(out["dir"])
    con = duckdb.connect()
    n_all = con.execute(f"SELECT count(*) FROM '{(d / 'scores.parquet').as_posix()}' WHERE scope = 'all'").fetchone()[0]
    assert n_all == independent_U(snap)  # A2: one score row per wallet in U(all), non-profiled included
    n_sig = con.execute(f"SELECT count(*) FROM '{(d / 'signals.parquet').as_posix()}' WHERE scope = 'all'").fetchone()[0]
    assert n_sig == 8 * n_all            # A3
    nonprof = con.execute(f"SELECT count(*) FROM '{(d / 'universe.parquet').as_posix()}' WHERE scope='all' AND NOT profiled").fetchone()[0]
    assert nonprof > 0
    run = json.loads((d / "run.json").read_bytes())
    assert abs(sum(run["weights_applied"].values()) - 1) < 1e-12 and run["weights_pre_norm_sum"] > 0
    assert run["snapshots"][0]["manifest_sha256"] == hashlib.sha256(b"{}").hexdigest()


def test_a4_recompute_detects_altered_weight(tmp_path):
    snap, wf, lock = world(tmp_path)
    out = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs")
    d = Path(out["dir"])
    run = json.loads((d / "run.json").read_bytes())
    con = duckdb.connect()
    con.execute(f"CREATE TABLE signals_dense AS SELECT * FROM '{(d / 'signals.parquet').as_posix()}'")
    con.execute(f"CREATE TABLE scores_final AS SELECT * FROM '{(d / 'scores.parquet').as_posix()}'")
    score.recompute_check(con, run["weights_applied"])                      # stored values reproduce
    altered = dict(run["weights_applied"], S7=run["weights_applied"]["S7"] + 0.1)
    with pytest.raises(score.ScoreError):
        score.recompute_check(con, altered)


def test_lock_refuses_edited_weights(tmp_path):
    snap, wf, lock = world(tmp_path)
    wf.write_text(WEIGHTS.replace("S3 = 1.0", "S3 = 2.0"))
    with pytest.raises(score.ScoreError):
        score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs")


def test_override_is_isolated_and_separately_hashed(tmp_path):
    snap, wf, lock = world(tmp_path)
    base = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(base["dir"]).iterdir()}
    ov = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs", overrides={"S1": 0}, tag="s1_zeroed", base_run_id=base["run_id"])
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(base["dir"]).iterdir()}
    assert before == after and ov["dir"] != base["dir"]
    rb, ro = (json.loads((Path(x["dir"]) / "run.json").read_bytes()) for x in (base, ov))
    assert rb["weights_sha256"] != ro["weights_sha256"] and ro["base_run_id"] == base["run_id"]
    assert ro["override_json"] == {"S1": 0}


def test_shuffle_mode_breaks_timing_keeps_s3(tmp_path):
    snap, wf, lock = world(tmp_path)
    base = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs")
    sh = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs", shuffle_seed=7, tag="shuffle_7")
    q = lambda d, s: dict(duckdb.connect().execute(
        f"SELECT proxy_wallet, component FROM '{(Path(d) / 'signals.parquet').as_posix()}' WHERE scope='all' AND signal_id='{s}'").fetchall())
    assert q(base["dir"], "S3") == q(sh["dir"], "S3")                        # non-timing signal unchanged
    s7b, s7s = q(base["dir"], "S7"), q(sh["dir"], "S7")
    assert s7b[W(10)] == pytest.approx(0.6)
    assert s7b != s7s                                                        # timing destroyed changes S7
    run = json.loads((Path(sh["dir"]) / "run.json").read_bytes())
    assert run["params_json"]["perturbation"] == {"type": "shuffle_ts", "seed": 7, "unit": "condition,walk"}


def test_run_dir_contract(tmp_path):
    """QA run-dir contract (design note r8): required run.json keys + one parquet per table, each with a scope column."""
    snap, wf, lock = world(tmp_path)
    out = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs", tag="contract")
    d = Path(out["dir"])
    run = json.loads((d / "run.json").read_bytes())
    for k in ("run_id", "tag", "weights_sha256", "weights_applied_json", "params_file_sha256", "snapshots_json",
              "event_times_sha256", "bypass_list_sha256", "prefilter_rule", "prefilter_cut", "mode", "params_json"):
        assert k in run, k
    assert set(run["weights_applied_json"]) == {f"S{i}" for i in range(1, 9)} | {"pre_norm_sum"}
    assert all("manifest_sha256" in x and "id" in x for x in run["snapshots_json"])
    con = duckdb.connect()
    for name in ("universe", "signals", "scores", "trades_summary"):
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM '{(d / (name + '.parquet')).as_posix()}'").fetchall()]
        assert "scope" in cols, name
    ts = con.execute(f"SELECT walk, fills_any_ts FROM '{(d / 'trades_summary.parquet').as_posix()}' WHERE scope='all' ORDER BY walk").fetchall()
    assert ts == [("all", 13), ("taker", 13)]
    assert run["contract"] == "run-dir v1.2 (design note r9)"
    tsw = (d / "trades_summary_wallet.parquet").as_posix()
    nu = con.execute(f"SELECT count(*) FROM '{(d / 'universe.parquet').as_posix()}'").fetchone()[0]
    assert con.execute(f"SELECT count(*) FROM '{tsw}'").fetchone()[0] == 2 * nu          # |U(scope)| x walks, every scope
    assert con.execute(f"SELECT count(DISTINCT (scope, walk, proxy_wallet)) FROM '{tsw}'").fetchone()[0] == 2 * nu
    assert con.execute(f"SELECT sum(fills_any_ts) FROM '{tsw}' WHERE scope='all' AND walk='all'").fetchone()[0] == 13
    # maker-only wallet (planted below via a second run): its taker row must exist with zeros
    tr2 = [("m1", 0, W(10 + i), "BUY", 2000 / 0.12, 0.12, RES - 10 * H + i * 60) for i in range(4)]
    tr2.append(("m1", 1, W(77), "SELL", 100, 0.5, RES - 3 * H, ("all",)))
    snap2 = make_snap(tmp_path / "s2", tr2)
    out2 = score.run(snap2, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs2", tag="contract2")
    tsw2 = (Path(out2["dir"]) / "trades_summary_wallet.parquet").as_posix()
    assert con.execute(f"SELECT fills_any_ts FROM '{tsw2}' WHERE scope='all' AND walk='taker' AND proxy_wallet=?", [W(77)]).fetchone() == (0,)


def _prof_dir(root, wallet, shape):
    """Minimal PROF dir: one profiled wallet with a winning bet redeemed 2 h after resolution, dormant, snapshot +90 d."""
    d = root / "PROF-T"
    (d / "derived").mkdir(parents=True)
    (d / "manifest.json").write_bytes(b"{}")
    pq.write_table(pa.table({"proxy_wallet": [wallet], "first_trade_unix": pa.array([RES - 86400], pa.int64()),
                             "first_funding_unix": pa.array([RES - 90000], pa.int64()), "lifetime_volume_usdc": [1e5],
                             "activity_status": ["ok"], "stats_status": ["ok"], "transfers_status": ["ok"],
                             "t_snap": ["2026-06-01T00:00:00.000000Z"], "transfers_status_detail": ["ok"],
                             "first_funding_block": pa.array([5_000_000], pa.int64()), "funding_truncated": [False],
                             "lower_bound_ts_unix": pa.array([1590824836], pa.int64())}), d / "derived" / "wallet_profile.parquet")
    pq.write_table(pa.table({"proxy_wallet": [wallet], "condition_id": ["m1"], "ts_unix": pa.array([RES + 7200], pa.int64()),
                             "shape": [shape], "tx_hash": ["0xabc"]}), d / "derived" / "redeems.parquet")
    pq.write_table(pa.table({"proxy_wallet": [wallet], "condition_id": ["m1"], "next_trade_unix": pa.array([None], pa.int64())}),
                   d / "derived" / "trade_after.parquet")
    pq.write_table(pa.table({"proxy_wallet": [wallet], "direction": ["in"], "hop": pa.array([1], pa.int32()), "counterparty": ["0xf"],
                             "token": ["t"], "amount": [1.0], "n_logs": pa.array([1], pa.int64()), "first_ts_unix": pa.array([1], pa.int64()),
                             "tx_hash": ["0x1"], "log_index": pa.array([1], pa.int64())}), d / "derived" / "transfers.parquet")
    return d


def test_relay_hub_counts_only_when_decode_verified(tmp_path):
    snap, wf, lock = world(tmp_path)
    pf2 = tmp_path / "params_v2.toml"
    pf2.write_text(PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa", "relay_hub", "factory_direct"]\n'
                   'relay_hub_requires_decode = true\n')
    wallet = W(11)   # cluster wallet whose ONLY winning position is m1 (W(10) also wins m2 without a redeem -> max gives 0)
    prof = _prof_dir(tmp_path, wallet, "relay_hub")
    s8 = lambda d: duckdb.connect().execute(f"SELECT component, na_reason FROM '{(Path(d) / 'signals.parquet').as_posix()}' "
                                            f"WHERE scope='all' AND proxy_wallet=? AND signal_id='S8'", [wallet]).fetchone()
    r1 = score.run(snap, wf, pf2, prof, lock=lock, runs_dir=tmp_path / "r1")
    assert s8(r1["dir"]) == (None, "auto_redeem_unknown")                        # no side-car -> unverified
    (prof / "s8check").mkdir()
    pq.write_table(pa.table({"proxy_wallet": [wallet], "condition_id": ["m1"], "shape_v2": ["relay_hub"], "decode_ok": [False]}),
                   prof / "s8check" / "shapes_v2.parquet")
    r2 = score.run(snap, wf, pf2, prof, lock=lock, runs_dir=tmp_path / "r2")
    assert s8(r2["dir"]) == (None, "auto_redeem_unknown")                        # decode failed
    pq.write_table(pa.table({"proxy_wallet": [wallet], "condition_id": ["m1"], "shape_v2": ["relay_hub"], "decode_ok": [True]}),
                   prof / "s8check" / "shapes_v2.parquet")
    r3 = score.run(snap, wf, pf2, prof, lock=lock, runs_dir=tmp_path / "r3")
    assert s8(r3["dir"])[1] is None and s8(r3["dir"])[0] > 0                    # verified -> counted


def test_signal_rows_carry_full_params_file_hash(tmp_path):
    """Two params files differing only in a [fetch] key must give different signals.params_hash (QA provenance ruling)."""
    snap, wf, lock = world(tmp_path)
    pa_ = tmp_path / "pA.toml"; pb_ = tmp_path / "pB.toml"
    pa_.write_text(PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa"]\n')
    pb_.write_text(PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa", "relay_hub", "factory_direct"]\n')
    ra = score.run(snap, wf, pa_, lock=lock, runs_dir=tmp_path / "ra")
    rb = score.run(snap, wf, pb_, lock=lock, runs_dir=tmp_path / "rb")
    h = lambda r: duckdb.connect().execute(f"SELECT DISTINCT params_hash FROM '{(Path(r['dir']) / 'signals.parquet').as_posix()}'").fetchall()
    assert h(ra) == [(hashlib.sha256(pa_.read_bytes()).hexdigest(),)]
    assert h(rb) == [(hashlib.sha256(pb_.read_bytes()).hexdigest(),)]
    assert h(ra) != h(rb)
    assert json.loads((Path(ra["dir"]) / "run.json").read_bytes())["params_file_sha256"] == h(ra)[0][0]


def test_export_writes_no_signed_zero(tmp_path, monkeypatch):
    """Tester R-3: the export-side `+ 0.0` on signals raw/component is tested on the WRITTEN parquet (break: drop it -> red).
    Scores: total_scorer = list_sum(...) and DuckDB's sum/list_sum of only -0.0 terms returns +0.0 (measured), so a total
    cannot be -0.0; the scores-side `+ 0.0` is a guard that no input can exercise. Its assertion below documents it only."""
    import math
    import pyarrow.parquet as pq
    from radar import signals as sg
    snap, wf, lock = world(tmp_path)
    real = sg.compute_all

    def forced(con, P, **kw):
        real(con, P, **kw)
        con.execute("""UPDATE signals_dense SET raw_value = -0.0::DOUBLE, component = -0.0::DOUBLE, na_reason = NULL
                       WHERE scope = 'all' AND proxy_wallet = ?""", [W(30)])
        # EVERY zero becomes -0.0: the parquet dictionary keeps the first-seen sign per row group, so a +0.0 elsewhere
        # would mask a missing canonicalisation (the first version of this test was vacuous for exactly that reason)
        con.execute("UPDATE signals_dense SET raw_value = -0.0::DOUBLE WHERE raw_value = 0")
        con.execute("UPDATE signals_dense SET component = -0.0::DOUBLE WHERE component = 0")
    monkeypatch.setattr(score.signals, "compute_all", forced)
    out = score.run(snap, wf, PARAMS_FILE, lock=lock, runs_dir=tmp_path / "runs", tag="negzero")
    d = Path(out["dir"])
    neg = lambda xs: sum(1 for x in xs if x is not None and x == 0.0 and math.copysign(1.0, x) < 0)
    t = pq.read_table(d / "signals.parquet").to_pydict()
    assert neg(t["raw_value"]) == 0 and neg(t["component"]) == 0
    s = pq.read_table(d / "scores.parquet").to_pydict()
    assert neg(s["total_scorer"]) == 0 and neg(s["total_pipeline"]) == 0
    row = [i for i, (sc, w) in enumerate(zip(s["scope"], s["proxy_wallet"])) if sc == "all" and w == W(30)]
    assert row and s["total_scorer"][row[0]] == 0.0                     # the planted wallet is present with total 0
