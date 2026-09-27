"""Anomaly Radar FIXTURE run for the UI Tester's planted controls (G6 §3). Never mixed into real data: its own run id,
context.fixture = true, a fixture banner, synthetic wallets/markets only. Banned-word plants are assembled at run
time so the source file stays clean.

Plants: C0-11 x2 (lazy script body + API body), C0-12 x3 (S6 positive on an incomplete path; S6/S1 NA
transfers_unverified; PROF-001 figure not final), C0-2 (pseudonym value in the raw wallet file), C0-4 (S5 NA, total > 0),
C0-5 (G5-passed flag with a different weights hash), C0-6 (na_reason 'zz_test'), C0-8 (share button),
C0-10/C3-3 (below-prefilter wallet with a stored S1 value), V4-1..3 (ego view: incomplete-marker link, hop-2 link,
stop-listed counterparty that must not render), V4-4 (0x..03 / 0x..04: S6 raw 0 while their link lists name 0x..01 ->
the S6-vs-list mismatch flag must fire; deliberate one-sided count).
usage: python make_fixture.py <out_dir>
"""
import hashlib
import json
import sys
from pathlib import Path

W = lambda n: "0x" + f"{n:040x}"
COND = "0x" + "f1" * 32
BANNED = "ins" + "ider"          # assembled: the literal never appears in source
SIG = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
WEIGHTS = "ab" * 32


def sig(**over):
    base = {k: {"component": 0.1, "raw": 0.1, "na_reason": None} for k in SIG}
    base["S3"] = {"component": 0.5, "raw": 5000.0, "na_reason": None}
    base["S6"] = {"component": 0.0, "raw": 0.0, "na_reason": None, "incomplete_path": False}
    for k, v in over.items():
        base[k] = {**base[k], **v}
    return base


def row(rank, w, score, signals, pp=True, nna=None, **extra):
    nna = sum(1 for s in signals.values() if s["na_reason"]) if nna is None else nna
    consistent = sum(0.125 * s["component"] for s in signals.values() if not s["na_reason"] and s["component"] is not None)
    return {"rank": rank, "wallet": w, "score": consistent if score is None else score, "n_signals_na": nna, "prefilter_pass": pp,
            "signals": signals, "s3_low_odds_stake_usdc": signals["S3"]["raw"], "markets_hit": 1, **extra}


def write(p: Path, obj) -> str:
    body = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def main(out: Path):
    rows = [
        row(1, W(1), None, sig(S6={"component": 0.4, "raw": 2.0, "incomplete_path": True})),                 # C0-12a
        row(2, W(2), None, sig(S1={"component": None, "raw": None, "na_reason": "transfers_unverified"},
                               S6={"component": None, "raw": None, "na_reason": "transfers_unverified"})),    # C0-12b
        row(3, W(3), None, sig(S5={"component": None, "raw": None, "na_reason": "n_resolved<min"})),        # C0-4
        row(4, W(4), None, sig(S2={"component": None, "raw": None, "na_reason": "zz_test"})),              # C0-6
        row(5, W(5), None, sig()),                                                                         # C0-2 wallet
        row(7, W(7), 0.95, sig()),                                   # A2 plant: stored total != sum of products -> mismatch flag
        row(8, W(8), None, sig(S5={"component": 0.02, "raw": 0.111, "na_reason": None})),                  # C3-1 wallet (5/5 at 0.95)
        row(9, W(9), None, sig(), scope_origin="all_ext"),                                                 # C1-2 plant: ext wallet in 'all'
    ]
    rows.sort(key=lambda r: (-r["score"], r["wallet"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    ctx = {"run_id": "fixture-plants-001", "started_at": "fixture", "finished_at": "fixture", "weights_sha256": WEIGHTS,
           "weights_applied": {k: 0.125 for k in SIG}, "params_file_sha256": "cd" * 32,
           "snapshots": [{"id": "SNAP-FIXTURE", "manifest_sha256": "ef" * 32}, {"id": "SNAP-FIXTURE/step4", "manifest_sha256": "01" * 32}],
           "event_times_sha256": None, "prefilter_rule": "fixture", "scope_limit_text": "fixture wallets only",
           "duckdb_threads": 1, "determinism_gate_pass": False, "run_json_sha256": "fixture",
           "profile_label": "PROF-001 (fixture)",                                                            # C0-12c
           "s5_min_bets": 3, "fixture": True, "run_label": "FIXTURE plants 001",
           "banner": "FIXTURE — planted test cases for the UI Tester, not data",
           "g5_passed_weights_sha256": "99" * 32, "g5_report_sha256": "88" * 32,                             # C0-5
           "plants": {"share_button": True,                                                                  # C0-8
                      "lazy_chunk": f"C0-11 plant 1: {BANNED} (lazy script body)",                            # C0-11 (1)
                      "api_body": f"C0-11 plant 2: {BANNED} (API response body)",                             # C0-11 (2)
                      "dom_tooltip": f"C0-1 plant: {BANNED} in a tooltip",                                      # C0-1
                      "footer_removed": True,                                                                 # C0-3 (leaderboard footer)
                      "scope_limit_removed": True,                                                            # C1-3
                      "price_label_removed": True}}                                                           # C3-5
    # V4 ego view plants: V4-1 link with the S6-incomplete marker (0x..01 <-> 0x..03), V4-2 hop-2 shared counterparty,
    # V4-3 a STOP-LISTED counterparty present in both wallets' raw lists that must NOT render (UI drops stop-list entries)
    CP1, CP2, CP3 = "0x" + "c1" * 20, "0x" + "c2" * 20, "0x" + "c3" * 20
    STOP_PLANT = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
    ctx["links"] = {"available": True, "max_hops": 3, "fanout_max": 20, "edges": ["in", "out"], "stop_list": [STOP_PLANT],
                    "stop_list_sha256": "fixture", "n_pairs": 2,
                    "rule": "fixture: two profiled wallets are linked when they share a counterparty (in or out, hop <= 3)"}
    files = {"context.json": write(out / "context.json", ctx),
             "leaderboard_all.json": write(out / "leaderboard_all.json", {"scope": "all", "n_ranked": len(rows), "n_universe": 6,
                                                                          "n_prefilter_pass": 5, "rows": rows}),
             "markets.json": write(out / "markets.json", {COND: {
                 "question": "Fixture market?", "event_id": "fx", "neg_risk": False, "closed_at_unix": 1772000000,
                 "resolution_ts_unix": 1772000000, "resolution_tx": "0x" + "00" * 32, "void": False, "winner_index": 0,
                 "t_ref_unix": 1771999999, "closed_at_delta_s": 0, "outcomes": {"0": "Yes", "1": "No"}}})}
    extra_markets = {  # cond -> (question, market fields, market_view fields)
        "0x" + "f2" * 32: ("Fixture void market? (C2-4)", {"void": True, "winner_index": None}, {}),
        "0x" + "f3" * 32: ("Fixture +136 s market? (C2-3)", {"closed_at_delta_s": 136}, {}),
        "0x" + "f4" * 32: ("Fixture Gamma-zero market? (C2-2)", {}, {"gamma_volume_shares_crosscheck": 0.0, "volume_taker_shares": 500.0}),
        "0x" + "f5" * 32: ("Fixture post-resolution fill market? (C2-5)", {}, {"post_resolution_taker_fills": 1}),
        "0x" + "f6" * 32: ("Fixture capped graph market? (C2-6)", {}, {"api_holders_count_crosscheck": 7, "top_n": 2}),
    }
    mk_all = json.loads((out / "markets.json").read_bytes())
    for c, (q, mf, _) in extra_markets.items():
        mk_all[c] = {**mk_all[COND], "question": q, **mf}
    files["markets.json"] = write(out / "markets.json", mk_all)
    fills = [[1771990000, COND, 0, 1, 100.0, 0.1, 0]]
    for r in rows:
        wf = {"wallet": r["wallet"], "scopes": {"all": {"score": r["score"], "n_signals_na": r["n_signals_na"], "prefilter_pass": True,
                                                        "signals": {k: {**v, "evidence": {}} for k, v in r["signals"].items()}}},
              "fills_columns": ["ts_unix", "condition_id", "outcome_index", "is_buy", "size_shares", "price_usdc", "post_resolution"],
              "fills": fills, "fills_note": "fixture"}
        if r["wallet"] == W(5):
            wf["pseudonym"] = "Plant Pseudonym C0-2"                                                         # C0-2 raw value
        if r["wallet"] == W(8):                                                                              # C3-1: 5/5 at 0.95
            wf["scopes"]["all"]["signals"]["S5"]["evidence"] = {"n": 5, "k": 5, "pval": 0.95 ** 5}
            wf["scopes"]["all"]["s5_mean_entry"] = 0.95
        write(out / "wallets" / f"{r['wallet']}.json", wf)
    below = {"wallet": W(6), "scopes": {"all": {"score": None, "n_signals_na": 4, "prefilter_pass": False,                # C0-10/C3-3
             "signals": {**{k: {"component": 0.2, "raw": 0.2, "na_reason": None, "evidence": {}} for k in SIG},
                         "S1": {"component": 0.9, "raw": 3.0, "na_reason": None, "evidence": {}}}}},
             "fills_columns": ["ts_unix", "condition_id", "outcome_index", "is_buy", "size_shares", "price_usdc", "post_resolution"],
             "fills": fills, "fills_note": "fixture"}
    write(out / "wallets" / f"{W(6)}.json", below)
    write(out / "prices" / f"{COND}.json", {"condition_id": COND, "source": "fixture", "resolution": "1 h VWAP",
                                           "series": {"0": [[1771990000, 0.1, 1], [1771996000, 0.2, 1]]}})
    write(out / "market_views" / f"{COND}.json", {"condition_id": COND, "volume_taker_shares": 100.0, "volume_taker_usdc": 10.0,
          "taker_rows": 1, "gamma_volume_shares_crosscheck": 0.0, "api_holders_count_crosscheck": 3, "post_resolution_taker_fills": 0,
          "node_rule": "fixture", "node_size": "net shares held at T_ref", "top_n": 150, "n_nodes": 1, "n_graph_nodes": 1,
          "graph_rule": "fixture", "nodes": [{"wallet": W(1), "shares_by_outcome": {"0": 100.0}, "shares": 100.0, "score": 0.9,
                                              "n_signals_na": 0, "prefilter_pass": True}],
          "graph_wallets": [W(1)], "edges": [], "n_s7_pairs_total": 0, "s6_edges_note": "fixture"})
    for c, (q, _, vf) in extra_markets.items():
        n3 = [{"wallet": W(k), "shares_by_outcome": {"0": 10.0 * k}, "shares": 10.0 * k, "score": 0.5 - 0.1 * k, "n_signals_na": 0,
               "prefilter_pass": True, "s6_incomplete": False} for k in (1, 2, 3)]
        top = [n["wallet"] for n in n3][: vf.get("top_n", 150)]
        write(out / "market_views" / f"{c}.json", {"condition_id": c, "volume_taker_shares": 100.0, "volume_taker_usdc": 10.0,
              "taker_rows": 3, "gamma_volume_shares_crosscheck": 100.0, "api_holders_count_crosscheck": 3, "post_resolution_taker_fills": 0,
              "node_rule": "fixture", "node_size": "net shares held at T_ref", "top_n": 150, "n_nodes": 3, "n_graph_nodes": len(top),
              "graph_rule": "fixture", "nodes": n3, "graph_wallets": top, "edges": [], "n_s7_pairs_total": 0, "s6_edges_note": "fixture",
              **{k: v for k, v in vf.items() if k != "top_n"}, **({"top_n": vf["top_n"]} if "top_n" in vf else {})})
        write(out / "prices" / f"{c}.json", {"condition_id": c, "source": "fixture", "resolution": "1 h VWAP", "series": {"0": [[1771990000, 0.3, 1]]}})
    score = {r["wallet"]: r["score"] for r in rows}
    link_files = {
        W(1): {"linked": [[W(3), 2, score[W(3)]], [W(4), 1, score[W(4)]]], "incomplete": True,                   # V4-1
               "cps": [[CP1, "in", 1], [CP2, "out", 2], [CP3, "in", 1], [STOP_PLANT, "in", 1]]},                  # V4-3 planted
        W(3): {"linked": [[W(1), 2, score[W(1)]]], "incomplete": False,
               "cps": [[CP1, "in", 1], [CP2, "in", 2], [STOP_PLANT, "out", 1]]},                                 # V4-2 hop 2
        W(4): {"linked": [[W(1), 1, score[W(1)]]], "incomplete": False, "cps": [[CP3, "in", 1]]},
    }
    for r in rows:
        if r["signals"]["S6"]["na_reason"]:
            continue                                                                                            # S6 NA: no file
        lf = link_files.get(r["wallet"], {"linked": [], "incomplete": False, "cps": []})
        write(out / "links" / f"{r['wallet']}.json", {"wallet": r["wallet"], "n_linked": len(lf["linked"]), **lf})
    (out / "manifest.json").write_bytes(json.dumps({"source_run_id": "fixture-plants-001", "fixture": True, "files": files},
                                                   indent=1, sort_keys=True).encode())
    print(json.dumps(files, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
