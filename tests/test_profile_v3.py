"""Profile writer under params v3 (hop-1 only, activity lookups recorded in the bundle and derived tables). No network."""
import json

import duckdb

from radar import activity, profile
from test_activity_lookup import FakeRPC, log as lk_log
from test_g4_profile_events import EXCH, WAL, api_handler, rpc_handler

P3 = {"s6_max_hops": 1, "s6_rule": "C1", "s6_activity_cap": 257}      # v3: no s6_hop_breadth key


def test_profile_wallet_runs_under_v3_params_hop1_only():
    rt, _ = rpc_handler()
    cl = profile.Clients(api_transport=api_handler(), rpc_transport=rt, sleep=lambda s: None)
    b = profile.profile_wallet(cl, WAL, ["c1"], stop={EXCH}, P=P3)
    hops = {e["hop"] for d in ("in", "out") for e in b["transfers"][d]["edges"]}
    assert hops == {1} and {f["hop"] for f in b["fetches"]} == {1}


def test_finalize_records_lookup_files_and_tables(tmp_path):
    rt, _ = rpc_handler()
    cl = profile.Clients(api_transport=api_handler(), rpc_transport=rt, sleep=lambda s: None)
    b = profile.profile_wallet(cl, WAL, ["c1"], stop={EXCH}, P=P3)
    b["writer_id"] = "e" * 64
    prof = tmp_path / "PROF-T"
    (prof / "raw").mkdir(parents=True)
    (prof / "raw" / f"{WAL}.json").write_bytes(json.dumps(b, sort_keys=True).encode())
    X = "0x" + "c1" * 20
    Ys = ["0x" + f"{i:040x}" for i in range(1, 4)]
    fake = FakeRPC([lk_log(Ys[0], X, 5), lk_log(X, Ys[1], 6), lk_log(Ys[2], X, 7)])
    res = activity.run_lookups(fake, prof, {X: (2, 2)}, to_block=100, cap=257, writer_id="e" * 64)
    assert res == {"lookup_set": 1, "fetched_now": 1}
    assert activity.run_lookups(fake, prof, {X: (2, 2)}, to_block=100, cap=257, writer_id="e" * 64)["fetched_now"] == 0   # resumable
    out = profile.finalize(prof, {"prof_id": "PROF-T", "snap_block": 50, "snap_instant_unix": 0})
    man = json.loads((prof / "manifest.json").read_bytes())
    kinds = {f["kind"] for f in man["files"]}
    assert kinds == {"bundle", "lookup"} and all(f["writer_id"] == "e" * 64 for f in man["files"])
    assert {d["file"] for d in out["derived"]} >= {"derived/lookups.parquet", "derived/lookup_y.parquet"}
    con = duckdb.connect()
    L = con.execute(f"SELECT address, status, capped, n_distinct, to_block FROM '{(prof / 'derived' / 'lookups.parquet').as_posix()}'").fetchall()
    Y = con.execute(f"SELECT y, first_block FROM '{(prof / 'derived' / 'lookup_y.parquet').as_posix()}' ORDER BY first_block").fetchall()
    assert L == [(X, "ok", False, 3, 100)] and Y == [(Ys[0], 5), (Ys[1], 6), (Ys[2], 7)]


# ---- Planner V1-6: a v3 profile is hop-1 only
def test_v3_params_refuse_hops_beyond_1():
    import pytest
    profile.check_v3_fetch_params({"s6_rule": "C1", "s6_max_hops": 1})
    with pytest.raises(profile.ProfileError, match="hop 1 only"):
        profile.check_v3_fetch_params({"s6_rule": "C1", "s6_max_hops": 2})
    profile.check_v3_fetch_params({"s6_max_hops": 3})                      # v2 params: unaffected


def test_v3_finalized_profile_has_no_hop2_records(tmp_path):
    rt, _ = rpc_handler()
    cl = profile.Clients(api_transport=api_handler(), rpc_transport=rt, sleep=lambda s: None)
    b = profile.profile_wallet(cl, WAL, ["c1"], stop={EXCH}, P=P3)
    b["writer_id"] = "e" * 64
    prof = tmp_path / "PROF-T"
    (prof / "raw").mkdir(parents=True)
    (prof / "raw" / f"{WAL}.json").write_bytes(json.dumps(b, sort_keys=True).encode())
    profile.finalize(prof, {"prof_id": "PROF-T", "snap_block": 50, "snap_instant_unix": 0})
    con = duckdb.connect()
    for f in ("transfers", "expansions"):
        assert con.execute(f"SELECT count(*) FROM '{(prof / 'derived' / (f + '.parquet')).as_posix()}' WHERE hop >= 2").fetchone()[0] == 0


# ---- QA V1-3/V1-4: dry-run lookup report inputs
def test_lookup_records_carry_dry_run_instrumentation_and_report(tmp_path):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "tools"))
    import dryrun_lookup_report
    X = "0x" + "c1" * 20
    logs = [lk_log("0x" + f"{i:040x}", X, 3 * i + 1) for i in range(1, 30)]
    rec = activity.lookup_activity(FakeRPC(logs, max_results=4), X, 200, cap=257)
    assert rec["calls"] >= 4 and rec["splits"]["result_count"] >= 1 and rec["splits"]["span"] == 0 and 0 < rec["max_ok_span"] <= 201
    prof = tmp_path / "PROF-T"
    (prof / "lookups").mkdir(parents=True)
    (prof / "lookups" / f"{X}.json").write_text(json.dumps({**rec, "writer_id": "e" * 64}))
    (prof / "manifest.json").write_text(json.dumps({"lookups": {"lookup_set": 1}, "calls_this_session": {"rpc": 9, "http_429": 0}}))
    r = dryrun_lookup_report.report(prof, project=19_876)
    assert r["N_L_after_hop1"] == 1 and r["calls_per_lookup_uncapped"]["n"] == 1 and r["calls_per_lookup_all"]["max"] == rec["calls"]
    assert r["bisection_trigger"]["result_count"] == rec["splits"]["result_count"] and r["projection"]["calls_p50"] == 19_876 * rec["calls"]
    assert r["projection"]["calls_mean"] == 19_876 * rec["calls"] and r["projection"]["calls_per_lookup_mean"] == rec["calls"]   # N4: mean


def test_dry_run_projection_uses_mean_with_p90_upper_and_p50(tmp_path):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "tools"))
    import dryrun_lookup_report
    prof = tmp_path / "PROF-T"
    (prof / "lookups").mkdir(parents=True)
    for i, calls in enumerate((2, 10, 4)):
        rec = {"address": f"0x{i:040x}", "status": "ok", "capped": False, "calls": calls, "splits": {"result_count": 0, "span": 0, "other": 0},
               "max_ok_span": 100, "elapsed_s": calls / 10}
        (prof / "lookups" / f"0x{i:040x}.json").write_text(json.dumps(rec))
    (prof / "manifest.json").write_text(json.dumps({"lookups": {"lookup_set": 3}, "calls_this_session": {"rpc": 16, "http_429": 0}}))
    pr = dryrun_lookup_report.report(prof, project=19_876)["projection"]
    assert pr["calls_per_lookup_mean"] == round(16 / 3, 3) and pr["calls_mean"] == round(19_876 * 16 / 3)
    assert pr["calls_per_lookup_p90"] == 10 and pr["calls_p90_upper"] == 19_876 * 10                     # upper bound: ceil index
    assert pr["calls_per_lookup_p50"] == 4 and pr["calls_p50"] == 19_876 * 4
    assert pr["serial_calls_per_s"] == 10.0 and pr["hours_mean"] == round(19_876 * 16 / 3 / 10 / 3600, 2)
