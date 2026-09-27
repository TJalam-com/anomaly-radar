"""Determinism gate (QA 2026-09-27): (i) A vs B threads=1 byte-identical, (ii) C threads=4 row-identical to A.
Synthetic SNAP large enough (1.2M fills over ~10 parquet row groups) for threads=4 to reorder partial aggregates, so an
order-dependent sum is visible to (ii). Break-harness mutations: float sums (ii red), random() in a signal (i red),
threads=2 + float sums (standing planted control)."""
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from radar import gate
from test_g4_score import PARAMS, RES, WEIGHTS

N = 1_200_000
N_COND = 40


def big_snap(root: Path) -> Path:
    snap = root / "SNAP-BIG"
    (snap / "derived").mkdir(parents=True)
    (snap / "step4").mkdir()
    D, S4 = (snap / "derived").as_posix(), (snap / "step4").as_posix()
    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute(f"""COPY (SELECT 'm' || i AS condition_id, 'e' || (i % 4) AS event_id, 0::BIGINT AS resolved_outcome_index
                   FROM range({N_COND}) t(i)) TO '{D}/markets.parquet' (FORMAT PARQUET)""")
    con.execute(f"""COPY (SELECT 'm' || i || '-t' || o AS token_id, 'm' || i AS condition_id, o::BIGINT AS outcome_index
                   FROM range({N_COND}) t(i), range(2) u(o)) TO '{D}/tokens.parquet' (FORMAT PARQUET)""")
    # deterministic pseudo-random fills; sizes exact at 6 dp, prices < 0.20 at 4 dp; plus exact-zero triples
    # (0.1 + 0.2 - 0.3) scattered across row groups
    con.execute(f"""COPY (
        SELECT 'pall' AS snapshot_id, i AS seq, 'all' AS walk, printf('0x%064x', i) AS tx_hash,
               'm' || (i % {N_COND}) AS condition_id, 'm' || (i % {N_COND}) || '-t' || ((i // {N_COND}) % 2) AS token_id,
               printf('0x%040x', (i * 2654435761) % 20000) AS proxy_wallet,
               CASE WHEN (i * 40503) % 5 = 0 THEN 'SELL' ELSE 'BUY' END AS side,
               ((i * 69069) % 50000000) / 1000000.0 + 0.000001 AS size,
               (1 + (i * 1103515245) % 1999) / 10000.0 AS price,
               to_timestamp({RES} - 10 - (i * 7919) % 5000000) AS ts
        FROM range({N}) t(i)
        UNION ALL
        SELECT 'pall', {N} + j, 'all', printf('0x%064x', {N} + j), 'm' || (j % {N_COND}), 'm' || (j % {N_COND}) || '-t0',
               printf('0x%040x', 30000 + (j // 3)), CASE WHEN j % 3 = 2 THEN 'SELL' ELSE 'BUY' END,
               [0.1, 0.2, 0.3][j % 3 + 1], 0.1, to_timestamp({RES} - 100 - j)
        FROM range(30000) t(j)
        ORDER BY seq) TO '{D}/trades.parquet' (FORMAT PARQUET)""")
    con.execute(f"""COPY (SELECT 'm' || i AS condition_id, '0x1' AS resolution_tx, 1::BIGINT AS resolution_log_index,
                   1::BIGINT AS resolution_block, {RES}::BIGINT AS resolution_ts_unix, '0x0' AS oracle, '[1, 0]' AS payouts_json,
                   FALSE AS void, 0::BIGINT AS chain_winner_index, {RES - 1}::BIGINT AS t_ref_unix, 0::BIGINT AS closed_at_delta_s
                   FROM range({N_COND}) t(i)) TO '{S4}/resolutions_chain.parquet' (FORMAT PARQUET)""")
    (snap / "manifest.json").write_bytes(b'{"finished_at": "2026-06-01T00:00:00Z"}')   # QA N1
    (snap / "step4" / "manifest.json").write_bytes(b"{}")
    return snap


def args(tmp_path: Path, snap: Path) -> list[str]:
    wf = tmp_path / "weights.toml"; wf.write_bytes(WEIGHTS.encode())
    (tmp_path / "weights.toml.lock").write_bytes(hashlib.sha256(wf.read_bytes()).hexdigest().encode())
    pf = tmp_path / "params.toml"
    pf.write_bytes((PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa"]\n').encode())
    (tmp_path / "params.toml.lock").write_bytes(hashlib.sha256(pf.read_bytes()).hexdigest().encode())
    return ["--snap", str(snap), "--weights", str(wf), "--params", str(pf), "--tag", "gate_test"]


def test_determinism_gate_passes(tmp_path):
    snap = big_snap(tmp_path)
    out = gate.run_gated(args(tmp_path, snap), tmp_path / "runs")
    assert out["pass"] and out["check_i_byte_identical_threads1"] and out["check_ii_row_identical_threads4"]
    g = json.loads((Path(out["dir"]) / "gate.json").read_bytes())
    assert g["pass"] and set(g["i"]) == set(gate.GATED) and g["C_threads"] == 4
    rj = json.loads((Path(out["dir"]) / "run.json").read_bytes())
    assert rj["duckdb_threads"] == 1


def test_gate_fails_and_voids_on_difference(tmp_path, monkeypatch):
    """The comparison itself can fail: a C run that differs in one value -> (ii) red, A renamed +VOID."""
    snap = big_snap(tmp_path)
    real = gate._score

    def fake(a, threads, runs_dir):
        d = real(a, threads, runs_dir)
        if threads == gate.C_THREADS:
            p = (d / "scores.parquet").as_posix()
            con = duckdb.connect()
            con.execute(f"CREATE TABLE s AS SELECT * FROM '{p}'")
            con.execute("UPDATE s SET total_scorer = total_scorer + 1e-12 WHERE rowid = 0")
            con.execute(f"COPY (SELECT * FROM s ORDER BY scope, proxy_wallet) TO '{p}' (FORMAT PARQUET)")
        return d
    monkeypatch.setattr(gate, "_score", fake)
    with pytest.raises(gate.GateError, match="ii=False"):
        gate.run_gated(args(tmp_path, snap), tmp_path / "runs")
    voids = [p for p in (tmp_path / "runs").iterdir() if p.name.endswith("+VOID")]
    assert len(voids) == 1 and not json.loads((voids[0] / "gate.json").read_bytes())["pass"]


def test_gate_check_i_fails_on_byte_difference_with_equal_rows(tmp_path, monkeypatch):
    """Tester R-4: B rewritten with the SAME rows but different bytes (other row-group size) -> (i) False, (ii) True,
    GateError and A renamed +VOID. Proves check (i) is a byte check that can fail on its own."""
    snap = big_snap(tmp_path)
    real = gate._score

    def fake(a, threads, runs_dir):
        d = real(a, threads, runs_dir)
        if threads == 1 and runs_dir.name == "B":
            p = (d / "signals.parquet").as_posix()
            con = duckdb.connect()
            con.execute(f"CREATE TABLE s AS SELECT * FROM '{p}'")
            con.execute(f"COPY (SELECT * FROM s ORDER BY scope, proxy_wallet, signal_id) TO '{p}' (FORMAT PARQUET, ROW_GROUP_SIZE 5000)")
        return d
    monkeypatch.setattr(gate, "_score", fake)
    with pytest.raises(gate.GateError, match=r"i=False, ii=True"):
        gate.run_gated(args(tmp_path, snap), tmp_path / "runs")
    voids = [p for p in (tmp_path / "runs").iterdir() if p.name.endswith("+VOID")]
    g = json.loads((voids[0] / "gate.json").read_bytes())
    assert len(voids) == 1 and not g["i"]["signals"]["equal"] and g["check_ii_row_identical_threads4"]
