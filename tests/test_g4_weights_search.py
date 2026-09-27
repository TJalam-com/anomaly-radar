"""Weight search (r5 Δ3 / r6 Δ1 bootstrap, QA P12). Synthetic look file only; the real look file is never used in tests."""
import hashlib
import json
from pathlib import Path

import pytest

from radar import weights_search as ws
from test_g4_score import PARAMS, RES, H, W, make_snap


def params_file(tmp_path):
    pf = tmp_path / "params.toml"
    pf.write_bytes((PARAMS + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa"]\n').encode())
    (tmp_path / "params.toml.lock").write_bytes(hashlib.sha256(pf.read_bytes()).hexdigest().encode())
    return pf


def setup(tmp_path, big_negatives=True):
    tr = []
    for i in range(4):   # positives: coordinated low-odds cluster (S7 = 0.6, S3 ~ 0.15)
        tr.append(("m1", 0, W(10 + i), "BUY", 2000 / 0.12, 0.12, RES - 10 * H + i * 60))
    for i in range(6):   # negatives: big lone low-odds buys (S3 ~ 0.89, S7 = 0) or small ones
        stake = 60_000 if big_negatives else 1_500
        tr.append(("m2", 1, W(40 + i), "BUY", stake / 0.1, 0.1, RES - 200 * H + i * 7000))
    snap = make_snap(tmp_path, tr)
    pf = params_file(tmp_path)
    look = tmp_path / "look.csv"
    lines = ["wallet,label,unit"] + [f"{W(10 + i)},pos,u1" for i in range(4)] + [f"{W(40 + i)},neg,n{i}" for i in range(6)]
    look.write_text("\n".join(lines) + "\n")
    return snap, pf, look, hashlib.sha256(look.read_bytes()).hexdigest()


def go(tmp_path, snap, pf, look, sha, **kw):
    return ws.run_search(snap, pf, None, look, sha, ("wallet", "label", "unit"), ("pos", "neg"), B=200,
                         runs_dir=tmp_path / "runs", out_weights=tmp_path / "w.toml", **kw)


def test_planted_config_wins_every_resample(tmp_path):
    snap, pf, look, sha = setup(tmp_path)
    r = go(tmp_path, snap, pf, look, sha)
    log = [json.loads(l) for l in Path(r["log"]).read_bytes().decode().splitlines()]
    names = [e["config"] for e in log]
    assert names[0] == "W0" and len(log) == 1 + 2 * 2          # computable = S3, S7 -> 1 + 2m
    w0 = log[0]
    assert w0["auc_scorer"] == 0.0                              # big negatives outrank the cluster under W0
    assert w0["n_pos_units"] == 1 and w0["n_neg_units"] == 6      # positives resampled as ONE cluster unit
    assert r["chosen"] == "DBL_S7"                              # LOO_S3 ties on p05; smaller L1 to W0 wins
    assert (tmp_path / "w.toml.lock").read_bytes().decode() == hashlib.sha256((tmp_path / "w.toml").read_bytes()).hexdigest()


def test_w0_kept_when_nothing_beats_it(tmp_path):
    snap, pf, look, sha = setup(tmp_path, big_negatives=False)   # W0 already separates perfectly -> no dAUC > 0
    r = go(tmp_path, snap, pf, look, sha)
    assert r["chosen"] == "W0"


def test_refuses_wrong_sha_and_missing_column(tmp_path):
    snap, pf, look, sha = setup(tmp_path)
    with pytest.raises(ws.SearchError):
        go(tmp_path, snap, pf, look, "0" * 64)
    with pytest.raises(ws.SearchError):
        ws.run_search(snap, pf, None, look, sha, ("wallet", "label", "cluster_id"), ("pos", "neg"), B=10, runs_dir=tmp_path / "runs")


def test_auc_ties_and_none():
    assert ws.auc([1.0], [1.0]) == 0.5
    assert ws.auc([None], [0.0]) == 0.0 and ws.auc([0.0], [None]) == 1.0


def setup_mixed(tmp_path):
    """One big negative among small ones: every config's dAUC depends on whether a resample draws it."""
    tr = [("m1", 0, W(10 + i), "BUY", 2000 / 0.12, 0.12, RES - 10 * H + i * 60) for i in range(4)]
    tr += [("m2", 1, W(40 + i), "BUY", (60_000 if i == 0 else 1_500) / 0.1, 0.1, RES - 200 * H + i * 7000) for i in range(6)]
    snap = make_snap(tmp_path, tr)
    pf = params_file(tmp_path)
    look = tmp_path / "look.csv"
    look.write_text("\n".join(["wallet,label,unit"] + [f"{W(10 + i)},pos,u1" for i in range(4)] + [f"{W(40 + i)},neg,n{i}" for i in range(6)]) + "\n")
    return snap, pf, look, hashlib.sha256(look.read_bytes()).hexdigest()


def test_mean_positive_but_p05_not_keeps_w0(tmp_path):
    """One big negative among small ones: configs beat W0 only in resamples that contain it -> mean dAUC > 0,
    5th percentile = 0 -> W0 must be kept (a mean-based rule would select noise)."""
    snap, pf, look, sha = setup_mixed(tmp_path)
    r = go(tmp_path, snap, pf, look, sha)
    log = {e["config"]: e for e in map(json.loads, Path(r["log"]).read_bytes().decode().splitlines())}
    assert log["DBL_S7"]["boot"]["mean_dAUC"] > 0 and log["DBL_S7"]["boot"]["p05"] <= 0
    assert r["chosen"] == "W0"


# ---------------------------------------------------------------- determinism gate (QA 2026-09-27)
def test_selection_gate_passes_and_base_run_is_gated(tmp_path):
    """Resample-sensitive world (setup_mixed): an unseeded bootstrap changes mean_dAUC between the two passes."""
    snap, pf, look, sha = setup_mixed(tmp_path)
    r = go(tmp_path, snap, pf, look, sha)
    g = r["selection_gate"]
    assert g["pass"] and set(g["files"]) == set(ws.GATED_OUT)
    assert all(v["A"] == v["B"] for v in g["files"].values())
    assert json.loads((Path(g["base_run"]) / "gate.json").read_bytes())["pass"]
    assert b"base_run_id" not in Path(r["log"]).read_bytes()          # no run id / clock inside gated bytes


def test_bootstrap_rng_is_constructed_with_the_given_seed(tmp_path, monkeypatch):
    """Deterministic detector for the selection gate's seeding. The end-to-end byte gate above detects an unseeded resample only
    probabilistically on small fixtures (measured: 19/20), and a probabilistic detector is not a check (QA)."""
    snap, pf, look, sha = setup(tmp_path)
    r = go(tmp_path, snap, pf, look, sha)
    seeds = []
    real = ws.random.Random

    class Recorder(real):
        def __init__(self, *a, **k):
            seeds.append(a[0] if a else k.get("x", "UNSEEDED"))
            super().__init__(*a, **k)
    monkeypatch.setattr(ws.random, "Random", Recorder)
    ws.select(Path(r["selection_gate"]["base_run"]), look, sha, ("wallet", "label", "unit"), ("pos", "neg"), 50, 12345, tmp_path / "sel")
    assert seeds == [12345]


def test_selection_gate_fails_and_voids_on_difference(tmp_path, monkeypatch):
    """The byte comparison can fail: selection B writes one extra byte -> SearchError, A renamed A+VOID."""
    snap, pf, look, sha = setup(tmp_path)
    real = ws._select_sub
    outs = []

    def fake(*a):
        res = real(*a)
        outs.append(Path(a[-1]))
        if len(outs) == 2:
            f = outs[1] / "weights_search.jsonl"
            f.write_bytes(f.read_bytes() + b" ")
        return res
    monkeypatch.setattr(ws, "_select_sub", fake)
    with pytest.raises(ws.SearchError, match="VOID"):
        go(tmp_path, snap, pf, look, sha)
    assert (outs[0].parent / "A+VOID").exists()
