"""(d') pass rule in tools/sweep_d4.py (QA pre-registration): pooled uniform p95 <= 1% with 0 mismatches; 0-uniform
markets retried (retry result counted); 'not exercised' markets < 5% of M taker size. Synthetic per-market results."""
import importlib.util
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

spec = importlib.util.spec_from_file_location("sweep_d4", Path(__file__).resolve().parent.parent / "tools" / "sweep_d4.py")
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def res(uni, allf=None, oc=0, ia=0):
    return {"tool_sha256": "t1", "neg_risk": False, "totals": {"chain_fills": allf or uni, "api_rows": allf or uni, "on_chain_not_api": oc, "in_api_not_chain": ia},
            "limits": {"chain_fills_in_uniform_windows": uni, "chain_fills_in_all_windows": allf or uni}, "rpc": {"http_200": 1}}


def build(tmp_path, results, sizes, retry=None):
    d = tmp_path / "derived"
    d.mkdir(parents=True)
    conds = list(results)
    pq.write_table(pa.table({"condition_id": conds}), d / "markets.parquet")
    pq.write_table(pa.table({"condition_id": conds, "walk": ["taker"] * len(conds), "size": [float(sizes[c]) for c in conds]}), d / "trades.parquet")
    out = tmp_path / "out"
    out.mkdir()
    for c, r in results.items():
        (out / f"{c}.json").write_text(json.dumps(r))
    for c, r in (retry or {}).items():
        (out / f"{c}.retry.json").write_text(json.dumps(r))
    sweep.main(["--out", str(out), "--snap", str(d), "--summarise-only"])
    return json.loads((out / "summary.json").read_text())["summary"]


def test_pass(tmp_path):
    s = build(tmp_path, {"a": res(200), "b": res(150), "c": res(0)}, {"a": 60, "b": 38, "c": 2}, retry={"c": res(0)})
    assert s["pooled_uniform_fills"] == 350 and s["pooled_p95_uniform"] <= 0.01
    assert s["not_exercised_list"] == ["c"] and abs(s["not_exercised_taker_size_share"] - 0.02) < 1e-12
    assert s["target_met"]


def test_fails_on_mismatch_short_n_or_big_unexercised(tmp_path):
    assert not build(tmp_path / "1", {"a": res(400, oc=1)}, {"a": 1})["target_met"]          # a mismatch
    assert not build(tmp_path / "2", {"a": res(250)}, {"a": 1})["target_met"]                # n < 299
    assert not build(tmp_path / "3", {"a": res(400), "b": res(0)}, {"a": 90, "b": 10}, retry={"b": res(0)})["target_met"]  # 10% unexercised


def test_retry_fills_count(tmp_path):
    s = build(tmp_path, {"a": res(300), "b": res(0)}, {"a": 50, "b": 50}, retry={"b": res(5)})
    assert s["not_exercised_count"] == 0 and s["pooled_uniform_fills"] == 305 and s["target_met"]


def test_mixed_tool_versions_fail(tmp_path):
    r2 = res(200); r2["tool_sha256"] = "t2"
    s = build(tmp_path, {"a": res(200), "b": r2}, {"a": 50, "b": 50})
    assert s["tool_sha256_set"] == ["t1", "t2"] and not s["target_met"]
