"""S6 v3 G derivation + the recorded-G gate in the scorer (tools/derive_g.py, radar/g_record.py, radar/score.py; D1 r16b §3)."""
import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from radar import score
from test_g4_score import PARAMS, RES, SNAP_MANIFEST, T_SNAP, W, WID, pins_file, world

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import derive_g  # noqa: E402

V3_S6 = """s6_rule = "C1"
s6_max_hops = 1
s6_fanout_max = 0
s6_activity_cap = 257
s6_graded_k = 20
s6_g_formula = "knee (test)"
s6_g_population = "natural-natural (test)"
"""
X, Z, Q = ("0x" + c * 40 for c in "abc")
SNAP_BLOCK = 500


def v3_params(tmp_path):
    body = "\n".join(l for l in PARAMS.splitlines() if not l.startswith(("s6_max_hops", "s6_full_at", "s6_fanout_max", "s6_hop_breadth")))
    pf = tmp_path / "params_v3.toml"
    pf.write_text(body + "\n" + V3_S6 + '[fetch]\nuser_signed_redeem_shapes = ["safe_exec", "direct_eoa"]\n')
    return pf


def v3_prof(root, natural_q_after_snap=True):
    """W(10), W(11) natural (low-odds cluster); W(30) not natural. X shared by W10+W11 (natural-natural); Z by W10+W30;
    Q by W10 (block 100) + W11 (block 900 > snap_block: not a sharer at SNAP)."""
    d = root / "PROF-T"
    (d / "derived").mkdir(parents=True)
    ws = [W(10), W(11), W(30)]
    pq.write_table(pa.table({"proxy_wallet": ws, "first_trade_unix": pa.array([RES - 86400] * 3, pa.int64()),
                             "first_funding_unix": pa.array([RES - 90000] * 3, pa.int64()), "lifetime_volume_usdc": [1e5] * 3,
                             "activity_status": ["ok"] * 3, "stats_status": ["ok"] * 3, "transfers_status": ["ok"] * 3,
                             "t_snap": ["2026-06-01T00:00:00Z"] * 3, "transfers_status_detail": ["ok"] * 3,
                             "first_funding_block": pa.array([50] * 3, pa.int64()), "funding_truncated": [False] * 3,
                             "lower_bound_ts_unix": pa.array([1590824836] * 3, pa.int64())}), d / "derived" / "wallet_profile.parquet")
    pq.write_table(pa.table({"proxy_wallet": pa.array([], pa.string()), "condition_id": pa.array([], pa.string()),
                             "ts_unix": pa.array([], pa.int64()), "shape": pa.array([], pa.string()), "tx_hash": pa.array([], pa.string())}),
                   d / "derived" / "redeems.parquet")
    pq.write_table(pa.table({"proxy_wallet": pa.array([], pa.string()), "condition_id": pa.array([], pa.string()),
                             "next_trade_unix": pa.array([], pa.int64())}), d / "derived" / "trade_after.parquet")
    tr = [(W(10), "in", X, 100), (W(11), "out", X, 200), (W(10), "in", Z, 100), (W(30), "in", Z, 100),
          (W(10), "in", Q, 100), (W(11), "in", Q, 900 if natural_q_after_snap else 300)]
    pq.write_table(pa.table({"proxy_wallet": [t[0] for t in tr], "direction": [t[1] for t in tr], "hop": pa.array([1] * len(tr), pa.int32()),
                             "counterparty": [t[2] for t in tr], "token": ["t"] * len(tr), "amount": [1.0] * len(tr),
                             "n_logs": pa.array([1] * len(tr), pa.int64()), "first_ts_unix": pa.array([1] * len(tr), pa.int64()),
                             "tx_hash": ["0x1"] * len(tr), "log_index": pa.array([1] * len(tr), pa.int64()), "via": [t[0] for t in tr],
                             "selected": [False] * len(tr), "stop_listed": [False] * len(tr), "amount_raw": ["1000000"] * len(tr),
                             "first_block": pa.array([t[3] for t in tr], pa.int64())}), d / "derived" / "transfers.parquet")
    ex = [(w, dr) for w in ws for dr in ("in", "out")]
    pq.write_table(pa.table({"proxy_wallet": [e[0] for e in ex], "direction": [e[1] for e in ex], "hop": pa.array([1] * 6, pa.int32()),
                             "parent": [None] * 6, "address": [e[0] for e in ex], "status": ["ok"] * 6, "n_logs": pa.array([1] * 6, pa.int64()),
                             "to_block": pa.array([1000] * 6, pa.int64()), "to_block_ts": pa.array([RES + 10**8] * 6, pa.int64()),
                             "gap_free": [True] * 6, "cache_hit": [False] * 6, "cache_fetched_at": [None] * 6,
                             "fetch_file": [f"fetch/{i}.json" for i in range(6)], "has_child_edges": [True] * 6}), d / "derived" / "expansions.parquet")
    lk = {X: 5, Z: 200, Q: 100}
    pq.write_table(pa.table({"address": list(lk), "status": ["ok"] * 3, "capped": [False] * 3, "cap_block": pa.array([None] * 3, pa.int64()),
                             "cap_ts": pa.array([None] * 3, pa.int64()), "from_block": pa.array([0] * 3, pa.int64()),
                             "to_block": pa.array([1000] * 3, pa.int64()), "n_distinct": pa.array(list(lk.values()), pa.int64()),
                             "cap": pa.array([257] * 3, pa.int64()),
                             "subranges_json": [json.dumps([{"from": 0, "to": 1000, "status": "ok"}])] * 3,
                             "n_sharers_fetch": pa.array([2] * 3, pa.int64()), "n_natural_sharers_fetch": pa.array([2] * 3, pa.int64())}),
                   d / "derived" / "lookups.parquet")
    ys = [(a, f"y{i}", 10) for a, n in lk.items() for i in range(n)]
    pq.write_table(pa.table({"address": [y[0] for y in ys], "y": [y[1] for y in ys], "first_block": pa.array([y[2] for y in ys], pa.int64()),
                             "first_ts": pa.array([None] * len(ys), pa.int64())}), d / "derived" / "lookup_y.parquet")
    (d / "raw").mkdir()
    for w in ws:
        (d / "raw" / f"{w}.json").write_bytes(json.dumps({"wallet": w, "writer_id": WID}).encode())
    files = [{"path": f.relative_to(d).as_posix(), "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "kind": "bundle", "writer_id": WID}
             for f in sorted((d / "raw").glob("*.json"))]
    derived = [{"file": f.relative_to(d).as_posix(), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted((d / "derived").glob("*.parquet"))]
    (d / "manifest.json").write_bytes(json.dumps({"prof_id": "PROF-T", "files": files, "derived": derived,
                                                  "snap_block": SNAP_BLOCK, "snap_instant_unix": T_SNAP,
                                                  "snap_manifest_sha256": hashlib.sha256(SNAP_MANIFEST).hexdigest()}).encode())
    return d


def setup(tmp_path, **kw):
    snap, wf, lock = world(tmp_path)
    pf = v3_params(tmp_path)
    (tmp_path / "params_v3.toml.lock").write_text(hashlib.sha256(pf.read_bytes()).hexdigest())
    prof = v3_prof(tmp_path, **kw)
    pins = pins_file(tmp_path, [f"PIN | PROF-T | {WID} | 2026-09-27T12:00Z | QA"])
    return snap, wf, lock, pf, prof, pins


def record(tmp_path, snap, prof, pf, name="g.json"):
    rec = derive_g.derive(snap, prof, pf)
    p = tmp_path / name
    p.write_bytes(json.dumps(rec, indent=1, sort_keys=True).encode())
    return rec, p, hashlib.sha256(p.read_bytes()).hexdigest()


def ledger(tmp_path, lines):
    p = tmp_path / "G_RECORDS.md"
    p.write_text("# QA-held G records (test)\n" + "".join(l + "\n" for l in lines))
    return p


def test_derive_g_population_is_natural_natural_at_snap(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, _, _ = record(tmp_path, snap, prof, pf)
    assert rec["population_n"] == 1 and rec["records_excluded"]["lt2_natural_sharers"] == 2     # Z (1 natural), Q (2nd edge post-SNAP)
    assert rec["G"] == 16 and rec["histogram"][2] == 1 and rec["snap_block"] == SNAP_BLOCK


def test_derive_g_counts_a_pre_snap_second_natural_edge(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path, natural_q_after_snap=False)
    assert record(tmp_path, snap, prof, pf)[0]["population_n"] == 2                            # Q now shared by 2 naturals at SNAP


def test_scorer_refuses_without_or_with_unrecorded_g_record(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    with pytest.raises(score.ScoreError, match="S6 v3 refused"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "r0", pins=pins, g_records=ledger(tmp_path, []))
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    with pytest.raises(score.ScoreError, match="not recorded"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "r1", pins=pins, g_record_path=gp, g_records=ledger(tmp_path, []))


def test_scorer_uses_the_recorded_g_and_reports_counts(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    led = ledger(tmp_path, [f"GREC | PROF-T | {gsha} | G={rec['G']} | 2026-09-27T12:30Z | QA"])
    out = score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "ok", pins=pins, g_record_path=gp, g_records=led)
    rj = json.loads((Path(out["dir"]) / "run.json").read_bytes())
    assert rj["s6_v3"]["G"] == 16 and rj["s6_v3"]["block_t"] == SNAP_BLOCK and rj["s6_v3"]["g_record"]["record_sha256"] == gsha
    assert rj["s6_v3"]["counts"]["all"]["n_lookup_set"] >= 1


@pytest.mark.parametrize("tamper", ["record_bytes", "ledger_g", "lookups_file", "params", "snap_block"])
def test_scorer_refuses_tampered_inputs(tmp_path, tamper):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    g = rec["G"]
    if tamper == "record_bytes":
        gp.write_bytes(gp.read_bytes() + b" ")
    if tamper == "ledger_g":
        g = rec["G"] + 1
    if tamper == "lookups_file":
        f = prof / "derived" / "lookup_y.parquet"
        pq.write_table(pa.table({"address": [X], "y": ["y0"], "first_block": pa.array([10], pa.int64()), "first_ts": pa.array([None], pa.int64())}), f)
        m = json.loads((prof / "manifest.json").read_bytes())      # re-signed manifest: the profile check passes, the G record binding must not
        for d in m["derived"]:
            if d["file"] == "derived/lookup_y.parquet":
                d["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
        (prof / "manifest.json").write_bytes(json.dumps(m).encode())
    if tamper == "params":
        pf2 = tmp_path / "params_v3b.toml"
        pf2.write_text(pf.read_text() + "# changed\n")
        (tmp_path / "params_v3b.toml.lock").write_text(hashlib.sha256(pf2.read_bytes()).hexdigest())
        pf = pf2
    if tamper == "snap_block":
        m = json.loads((prof / "manifest.json").read_bytes()); m["snap_block"] = SNAP_BLOCK + 1
        (prof / "manifest.json").write_bytes(json.dumps(m).encode())
    led = ledger(tmp_path, [f"GREC | PROF-T | {gsha} | G={g} | 2026-09-27T12:30Z | QA"])
    with pytest.raises(score.ScoreError, match="S6 v3 refused"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "t", pins=pins, g_record_path=gp, g_records=led)


REAL_LEDGER = Path(__file__).resolve().parent / "data" / "G_RECORDS_real_dcc92464.md"   # byte copy of ledger/G_RECORDS.md (QA, with R15V3-6/7 prose)


def test_real_ledger_copy_has_no_record_and_the_scorer_refuses(tmp_path):
    from radar import g_record
    assert hashlib.sha256(REAL_LEDGER.read_bytes()).hexdigest() == "dcc9246407ae514d65e894cbfd287d0ec8002e7e365dc30cc668c11fc1cec00c"
    assert g_record.recorded(REAL_LEDGER) == {}                      # the Format line, prose and "(none yet)" are not records
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)                 # a valid, correctly derived G record ...
    with pytest.raises(score.ScoreError, match="not recorded"):      # ... is refused until QA appends its GREC line
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "real", pins=pins, g_record_path=gp, g_records=REAL_LEDGER)


# ---- Planner V1-1 (score level): the in-edge mode uses the SAME recorded G; V1-2: a lookup retry needs a new G record; V1-6 guard
def test_in_edge_mode_uses_the_same_recorded_g_and_records_edge_mode(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    led = ledger(tmp_path, [f"GREC | PROF-T | {gsha} | G={rec['G']} | 2026-09-27T12:30Z | QA"])
    out = score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "in", pins=pins, g_record_path=gp, g_records=led, s6_edges=("in",), tag="in")
    s6 = json.loads((Path(out["dir"]) / "run.json").read_bytes())["s6_v3"]
    assert s6["edge_mode"] == ["in"] and s6["G"] == rec["G"] and s6["g_record"]["record_sha256"] == gsha


def _resign(prof, rel):
    m = json.loads((prof / "manifest.json").read_bytes())
    for d in m["derived"]:
        if d["file"] == rel:
            d["sha256"] = hashlib.sha256((prof / rel).read_bytes()).hexdigest()
    (prof / "manifest.json").write_bytes(json.dumps(m).encode())


def test_lookup_retry_requires_a_new_g_record(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    old = f"GREC | PROF-T | {gsha} | G={rec['G']} | 2026-09-27T12:30Z | QA"
    f = prof / "derived" / "lookup_y.parquet"                          # a retried lookup adds a counterparty -> different lookup records
    t = pq.read_table(f).to_pylist() + [{"address": X, "y": "y_retry", "first_block": 20, "first_ts": None}]
    pq.write_table(pa.Table.from_pylist(t, schema=pq.read_schema(f)), f)
    _resign(prof, "derived/lookup_y.parquet")
    with pytest.raises(score.ScoreError, match="lookup_y.parquet differs"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "a", pins=pins, g_record_path=gp, g_records=ledger(tmp_path, [old]))
    rec2, gp2, gsha2 = record(tmp_path, snap, prof, pf, name="g2.json")   # full cycle: new G record, recorded by QA
    led2 = ledger(tmp_path, [old, f"GREC | PROF-T | {gsha2} | G={rec2['G']} | 2026-09-27T13:30Z | QA"])
    assert score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "b", pins=pins, g_record_path=gp2, g_records=led2)["run_id"]
    with pytest.raises(score.ScoreError, match="superseded"):          # R15V3-7: the earlier line for PROF-T is no longer valid
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "c", pins=pins, g_record_path=gp, g_records=led2)


def test_v3_profile_with_hop2_rows_is_refused(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    f = prof / "derived" / "expansions.parquet"
    t = pq.read_table(f).to_pylist(); t.append(dict(t[0], hop=2, address="0x" + "9" * 40))
    pq.write_table(pa.Table.from_pylist(t, schema=pq.read_schema(f)), f)
    _resign(prof, "derived/expansions.parquet")
    led = ledger(tmp_path, [f"GREC | PROF-T | {gsha} | G={rec['G']} | 2026-09-27T12:30Z | QA"])
    with pytest.raises(score.ScoreError, match="hop >= 2"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "h", pins=pins, g_record_path=gp, g_records=led)


# ---- Tester 1 review R15V3-3 (SNAP binding), R15V3-4 (ledger path / g5_eligible), R15V3-6 (duplicate sha); QA: S4 input of record
def test_g_record_is_bound_to_the_snap(tmp_path):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    led = ledger(tmp_path, [f"GREC | PROF-T | {gsha} | G={rec['G']} | 2026-09-27T12:30Z | QA"])
    (snap / "manifest.json").write_bytes(b'{"finished_at": "2026-06-01T00:00:00Z", "other": "snap"}')   # same t, another SNAP
    with pytest.raises(score.ScoreError, match="SNAP differs"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "s", pins=pins, g_record_path=gp, g_records=led)
    (snap / "manifest.json").write_bytes(SNAP_MANIFEST)
    m = json.loads((prof / "manifest.json").read_bytes()); m["snap_manifest_sha256"] = "0" * 64   # the profile names another SNAP
    (prof / "manifest.json").write_bytes(json.dumps(m).encode())
    with pytest.raises(score.ScoreError, match="SNAP differs"):
        score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "p", pins=pins, g_record_path=gp, g_records=led)


def test_non_default_ledger_makes_the_run_non_g5_and_is_recorded(tmp_path, monkeypatch):
    snap, wf, lock, pf, prof, pins = setup(tmp_path)
    rec, gp, gsha = record(tmp_path, snap, prof, pf)
    led = ledger(tmp_path, [f"GREC | PROF-T | {gsha} | G={rec['G']} | 2026-09-27T12:30Z | QA"])
    rj = json.loads((Path(score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "n", pins=pins, g_record_path=gp,
                                    g_records=led)["dir"]) / "run.json").read_bytes())
    assert rj["g5_eligible"] is False and "non-default G ledger" in rj["g5_ineligible_reasons"][0]
    assert rj["s6_v3"]["ledger"] == {"path": str(led), "sha256": hashlib.sha256(led.read_bytes()).hexdigest()}
    monkeypatch.setattr(score, "G_RECORDS", led)                              # the same file AS the default ledger -> eligible
    rj2 = json.loads((Path(score.run(snap, wf, pf, prof, lock=lock, runs_dir=tmp_path / "d", pins=pins, g_record_path=gp,
                                     g_records=led)["dir"]) / "run.json").read_bytes())
    assert rj2["g5_eligible"] is True and rj2["g5_ineligible_reasons"] == []


def test_same_record_sha_with_different_fields_is_refused(tmp_path):
    from radar import g_record
    s = "a" * 64
    same = ledger(tmp_path, [f"GREC | PROF-T | {s} | G=63 | t1 | QA", f"GREC | PROF-T | {s} | G=63 | t1 | QA"])
    assert g_record.recorded(same)[s]["G"] == 63                               # an identical repeat is harmless
    diff = ledger(tmp_path, [f"GREC | PROF-T | {s} | G=63 | t1 | QA", f"GREC | PROF-T | {s} | G=31 | t2 | QA"])
    with pytest.raises(g_record.GRecordError, match="twice with different fields"):
        g_record.recorded(diff)


def test_only_the_last_grec_line_per_profile_is_valid(tmp_path):
    from radar import g_record
    a, b = "a" * 64, "b" * 64
    r = g_record.recorded(ledger(tmp_path, [f"GREC | PROF-T | {a} | G=63 | t1 | QA", f"GREC | PROF-X | {b} | G=31 | t2 | QA"]))
    assert not r[a]["superseded"] and not r[b]["superseded"]                  # different profiles: both valid
    r = g_record.recorded(ledger(tmp_path, [f"GREC | PROF-T | {a} | G=63 | t1 | QA", f"GREC | PROF-T | {b} | G=31 | t2 | QA"]))
    assert r[a]["superseded"] and not r[b]["superseded"]


def test_event_times_must_be_the_s4_input_of_record(tmp_path):
    snap, wf, lock = world(tmp_path)
    from test_g4_score import PARAMS_FILE
    ev = tmp_path / "ev.csv"
    ev.write_text("condition_id,event_ts_utc,event_ts_precision,event_tz,hedged_report_ts_utc,hedged_report_ts_precision,"
                  "hedged_report_tz,tier_gap,occurrence_ts_precision,event_ts_tz_inferred,hedged_report_tz_inferred\nm1,2026-02-28T06:15:00Z,minute,UTC,,,,false,minute,false,false\n")
    with pytest.raises(score.ScoreError, match="not the S4 input of record"):
        score.run(snap, wf, PARAMS_FILE, event_times=ev, lock=lock, runs_dir=tmp_path / "e1")
    out = score.run(snap, wf, PARAMS_FILE, event_times=ev, lock=lock, runs_dir=tmp_path / "e2",
                    event_times_sha=hashlib.sha256(ev.read_bytes()).hexdigest())
    rj = json.loads((Path(out["dir"]) / "run.json").read_bytes())
    assert rj["g5_eligible"] is False and "event-times pin overridden" in rj["g5_ineligible_reasons"][0]
