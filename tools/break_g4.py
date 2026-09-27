"""Break-test harness for tests/test_g4_signals.py: apply one mutation to radar/signals.py, run the named test,
expect FAILURE, restore byte-identical. Exit 0 only if every mutation turned its test red and the file was restored."""
import hashlib
import os
import pathlib
import subprocess
import sys

APP = pathlib.Path(__file__).resolve().parent.parent
MUTATIONS = [  # (name, old, new, test[, file])
    ("S3 floor removed", "{floorlog_sql(dbl(f'sum({stake()})'), P['s3_min_stake'], P['s3_cap_usdc'])}",
     "{floorlog_sql(dbl(f'sum({stake()})'), 1, P['s3_cap_usdc'])}", "test_s3_floorlog"),
    ("S5 raw win-rate instead of Poisson-binomial", "pval = poisson_binomial_tail(ps, k)", "pval = max(1e-300, 1 - k / len(ps))",
     "test_s5_poisson_binomial"),
    ("S7 time window dropped", "AND abs(a.t - b.t) <= {P['s7_window_s']}", "", "test_s7_coordination"),
    ("S4 after-event buys counted", "AND epoch(a.anchor_ts) - epoch(f.ts) > 0", "", "test_s4_anchor_window"),
    ("S1 freshness ramp constant", 'ramp_down(hours, P["s1_fresh_h"], P["s1_stale_h"])', "1.0", "test_s1_freshness_and_na"),
    ("S2 numerator BUY-only", "FROM signal_fills f JOIN scope_map s USING (condition_id) WHERE f.walk = 'all' GROUP BY 1, 2)",
     "FROM signal_fills f JOIN scope_map s USING (condition_id) WHERE f.walk = 'all' AND f.side = 'BUY' GROUP BY 1, 2)",
     "test_s2_concentration_both_sides"),
    ("S6 stop-list ignored", "AND t.counterparty NOT IN (SELECT address FROM stop_list)", "", "test_s6_stop_list_and_fanout"),
    ("S6 edge-direction flag ignored", "WHERE t.direction IN ({dirs})", "WHERE TRUE", "test_s6_in_and_out"),
    ("S8 tx-shape check dropped", 'elif shape not in P.get("_user_signed_shapes", USER_SIGNED_SHAPES):', "elif False:", "test_s8_exit_behaviour"),
    ("prefilter single-leg max instead of Σ", "SELECT f.proxy_wallet, sum({stake()}) AS s3_set_raw",
     "SELECT f.proxy_wallet, max({stake()}) AS s3_set_raw", "test_prefilter_bypass_and_density"),
    ("bypass ignored", "b.proxy_wallet IS NOT NULL AS bypass_listed", "FALSE AS bypass_listed", "test_prefilter_bypass_and_density"),
    # scorer (radar/score.py) -> tests/test_g4_score.py
    ("scores drop non-profiled wallets", "FROM scores_t s JOIN universe_t u USING (scope, proxy_wallet)\"\"\")",
     "FROM scores_t s JOIN universe_t u USING (scope, proxy_wallet) WHERE u.profiled\"\"\")", "test_g4_score.py::test_dense_scores_and_signals", "score"),
    ("weights lock not enforced", "if lock is not None and (not lock.exists() or lock.read_text().strip() != h):", "if False:",
     "test_g4_score.py::test_lock_refuses_edited_weights", "score"),
    ("override writes into base run dir",
     "    out = runs_dir / run_id\n    if out.exists():\n        raise ScoreError(f\"{out} exists — runs never overwrite\")\n    out.mkdir(parents=True)",
     "    out = runs_dir / (base_run_id or run_id)\n    out.mkdir(parents=True, exist_ok=True)",
     "test_g4_score.py::test_override_is_isolated_and_separately_hashed", "score"),
    ("shuffle seed ignored", "ORDER BY hash(snapshot_id || ':' || seq::VARCHAR || ':{int(shuffle_seed)}')", "ORDER BY snapshot_id, seq",
     "test_g4_score.py::test_shuffle_mode_breaks_timing_keeps_s3", "score"),
]
MUTATIONS += [
    ("S1 truncation rule ignored", "        if trunc:\n            # QA funding-window rule (corrected)",
     "        if False:\n            # QA funding-window rule (corrected)", "test_s1_funding_window_truncation"),
    ("S1 truncated -> always first_trade (rejected rule)", "if ftt is not None and lb_ts is not None and ftt < lb_ts:", "if ftt is not None:",
     "test_s1_funding_window_truncation"),
    ("S6 incomplete flag dropped", 'ev["edges_possibly_incomplete"] = True', "pass", "test_s1_funding_window_truncation"),
    ("S8 frozen shapes ignored (default incl. relay_hub)", 'elif shape not in P.get("_user_signed_shapes", USER_SIGNED_SHAPES):',
     'elif shape not in ("safe_exec", "relay_hub", "direct_eoa"):', "test_s8_relay_hub_not_user_signed_under_frozen_shapes"),
    ("profile truncation flag never set", 'truncated = (ff[1] is not None and ff[1] <= lb + win) or (ff[0] is None and act_status == "ok")',
     "truncated = False", "test_g4_profile_events.py::test_profile_truncation_flag", "profile"),
]
MUTATIONS += [
    ("search selects on mean dAUC", 'e["boot"]["p05"] is not None and e["boot"]["p05"] > 0', 'e["boot"]["mean_dAUC"] is not None and e["boot"]["mean_dAUC"] > 0',
     "test_g4_weights_search.py::test_mean_positive_but_p05_not_keeps_w0", "search"),
    ("search L1 tie-break removed", '-sum(abs(e["weights"][s] - w0[s]) for s in signals.SIGNALS)', "0",
     "test_g4_weights_search.py::test_planted_config_wins_every_resample", "search"),
    ("search look sha not checked", "    if sha != sha_expected:", "    if False:",
     "test_g4_weights_search.py::test_refuses_wrong_sha_and_missing_column", "search"),
    ("search resamples wallets not units", "units.setdefault((p, u), []).append(w)", "units.setdefault((p, w), []).append(w)",
     "test_g4_weights_search.py::test_planted_config_wins_every_resample", "search"),
]
MUTATIONS += [
    ("sweep rule 3 dropped", "rule3 = bool(ne_share is not None and ne_share < 0.05)", "rule3 = True",
     "test_sweep_summary.py::test_fails_on_mismatch_short_n_or_big_unexercised", "sweep"),
    ("sweep mismatches ignored", "rule1 = bool(p95 is not None and p95 <= 0.01 and mism == 0)", "rule1 = bool(p95 is not None and p95 <= 0.01)",
     "test_sweep_summary.py::test_fails_on_mismatch_short_n_or_big_unexercised", "sweep"),
    ("sweep retry result ignored", "        if fr.exists():\n            runs.append(json.loads(fr.read_bytes()))", "        pass",
     "test_sweep_summary.py::test_retry_fills_count", "sweep"),
]
MUTATIONS += [
    ("trades_summary_wallet drops zero rows", "             LEFT JOIN agg a ON a.scope = u.scope AND a.walk = w.walk AND a.proxy_wallet = u.proxy_wallet",
     "             JOIN agg a ON a.scope = u.scope AND a.walk = w.walk AND a.proxy_wallet = u.proxy_wallet",
     "test_g4_score.py::test_run_dir_contract", "score"),
    ("chain check: neg_risk ignored (always V1)", "    return EX_NEGRISK if neg_risk else EX_V1", "    return EX_V1",
     "test_chain_fill_check.py::test_exchange_by_neg_risk_and_override", "cfc"),
    ("chain check: override ignored", "    if override:\n        return override.lower()", "    if False:\n        return override.lower()",
     "test_chain_fill_check.py::test_exchange_by_neg_risk_and_override", "cfc"),
    ("relay_hub decode requirement ignored (no side-car)", "CASE WHEN shape = 'relay_hub' AND {str(bool(relay_requires_decode)).upper()} THEN 'relay_hub_unverified' ELSE shape END AS shape",
     "shape", "test_g4_score.py::test_relay_hub_counts_only_when_decode_verified", "score"),
    ("relay_hub decode_ok ignored (side-car)", "AND NOT coalesce(c.decode_ok, FALSE)", "AND FALSE",
     "test_g4_score.py::test_relay_hub_counts_only_when_decode_verified", "score"),
    ("s8 factory_direct without CREATE2 check", 'return ("factory_direct" if rd.create2_proxy(FACTORY, frm) == w else "other"), None',
     'return "factory_direct", None', "test_s8_shapes.py::test_factory_direct_requires_create2_match", "s8"),
    ("s8 relay decode result ignored", "        return \"relay_hub\", bool(sok and cok)", "        return \"relay_hub\", True",
     "test_s8_shapes.py::test_relay_hub_decode_flag_and_passthrough", "s8"),
    ("signals.params_hash hashes [params] only", 'con.execute("UPDATE signals_dense SET params_hash = ?", [params_sha])',
     'con.execute("UPDATE signals_dense SET params_hash = ?", [sha_bytes(json.dumps({k: v for k, v in P.items() if not k.startswith("_")}, sort_keys=True).encode())])',
     "test_g4_score.py::test_signal_rows_carry_full_params_file_hash", "score"),
    ("network block silently disabled (connect/DNS return None)", 'raise NetworkDisabled(f"network disabled in tests: connect{args}")', "return None",
     "test_no_network.py::test_planted_network_call_fails_loudly", "conftest"),
    ("pnl excludes post-resolution fills", "FROM '{D}/trades.parquet' t WHERE t.walk = 'all' GROUP BY 1, 2, 3),",
     "FROM '{D}/trades.parquet' t JOIN markets_r mm ON mm.condition_id = t.condition_id WHERE t.walk = 'all' AND t.ts < mm.chain_resolution_ts GROUP BY 1, 2, 3),",
     "test_pnl.py::test_planted_pnl_65_and_post_resolution_sell", "score"),
    ("pnl void not excluded", "CASE WHEN m.void THEN NULL ELSE sum(l.cash_out)", "CASE WHEN FALSE THEN NULL ELSE sum(l.cash_out)",
     "test_pnl.py::test_void_market_excluded", "score"),
    ("guard: pnl pattern dropped", '|pnl", re.I)', '", re.I)',
     "test_g4_guards.py::test_guard_grep_fires_on_planted_reference", "guards"),
    ("run-dir contract: tag key dropped", '"run_id": run_id, "tag": tag, "base_run_id": base_run_id,', '"run_id": run_id, "base_run_id": base_run_id,',
     "test_g4_score.py::test_run_dir_contract", "score"),
]
# ---- QA determinism ruling 2026-09-27: exact numerics, determinism gate, selection gate, standing planted control
STAKE_EXACT = 'return f"(CAST({a}.size AS DECIMAL(38,6)) * CAST({a}.price AS DECIMAL(38,10)))"'
STAKE_FLOAT = 'return f"({a}.size * {a}.price)"'
SZ_EXACT = 'return f"CAST({a}.size AS DECIMAL(38,6))"'
SZ_FLOAT = 'return f"{a}.size"'
MUTATIONS += [
    ("exact size -> float", SZ_EXACT, SZ_FLOAT, "test_exact_zero_net_position_is_not_a_bet"),
    ("exact stake -> float", STAKE_EXACT, STAKE_FLOAT, "test_prefilter_exact_sum_at_threshold"),
    ("DECIMAL->DOUBLE via direct cast (not correctly rounded)", 'return f"CAST(CAST({x} AS VARCHAR) AS DOUBLE)"', 'return f"CAST({x} AS DOUBLE)"',
     "test_prefilter_exact_sum_at_threshold"),
    ("prefilter threshold compared in DOUBLE", "coalesce(pf.s3_set_raw, 0) >= {dec('?')}", "CAST(coalesce(pf.s3_set_raw, 0) AS DOUBLE) >= ?",
     "test_prefilter_threshold_compared_exactly"),
    # gate (ii): float sums are order-dependent -> threads=4 run C differs from A ((i) stays green: documented in test_gate)
    ("gate (ii): DECIMAL dropped (float size + stake)", STAKE_EXACT, STAKE_FLOAT, "test_gate.py::test_determinism_gate_passes", "signals",
     [("signals", SZ_EXACT, SZ_FLOAT)]),
    # gate (i): a per-process random term in a signal -> A and B differ
    ("gate (i): per-process random() in S3 raw", "SELECT s.scope, f.proxy_wallet, 'S3', {dbl(f'sum({stake()})')},",
     "SELECT s.scope, f.proxy_wallet, 'S3', {dbl(f'sum({stake()})')} + random() * 1e-9,", "test_gate.py::test_determinism_gate_passes"),
    # standing planted control: deliberately nondeterministic build = threads=2 + float sums -> the gate must FAIL
    ("PLANTED CONTROL: threads=2 + float sums", 'con.execute(f"SET threads={int(threads)}")', 'con.execute("SET threads=2")',
     "test_gate.py::test_determinism_gate_passes", "score", [("signals", STAKE_EXACT, STAKE_FLOAT), ("signals", SZ_EXACT, SZ_FLOAT)]),
    # selection gate (i): unseeded resample -> the two selection processes write different JSONL
    ("selection gate (i): unseeded bootstrap resample", "    rng = random.Random(seed)", "    rng = random.Random()",
     "test_g4_weights_search.py::test_selection_gate_passes_and_base_run_is_gated", "search"),
    ("signed zero not canonicalised", 'float(r[2]) + 0.0 for r in rows', 'float(r[2]) for r in rows',
     "test_no_signed_zero_in_outputs"),
    ("export signed zero (signals) not canonicalised", "REPLACE (raw_value + 0.0 AS raw_value, component + 0.0 AS component)",
     "REPLACE (raw_value AS raw_value, component AS component)", "test_g4_score.py::test_export_writes_no_signed_zero", "score"),
    ("_sparse component signed zero not canonicalised", "float(r[3]) + 0.0 for r in rows", "float(r[3]) for r in rows",
     "test_sparse_canonicalises_signed_zero_in_raw_and_component"),
    ("gate: check (i) ignored", '    ok_i = all(v["equal"] for v in check_i.values())', "    ok_i = True",
     "test_gate.py::test_gate_check_i_fails_on_byte_difference_with_equal_rows", "gate"),
    ("chain check: comparison always 0", "        miss_api = chain - apiw      # on chain, not in API",
     "        miss_api = Counter()      # on chain, not in API", "test_chain_fill_check.py::test_detection_api_row_removed", "cfc",
     [("cfc", "        miss_chain = apiw - chain    # in API, not on chain", "        miss_chain = Counter()    # in API, not on chain")]),
    ("chain check: comparison sides swapped", "        miss_api = chain - apiw      # on chain, not in API",
     "        miss_api = apiw - chain      # on chain, not in API", "test_chain_fill_check.py::test_detection_api_row_removed", "cfc",
     [("cfc", "        miss_chain = apiw - chain    # in API, not on chain", "        miss_chain = chain - apiw    # in API, not on chain")]),
    ("gate: check (ii) ignored", "    ok_ii = all(v[\"equal\"] for v in check_ii.values())", "    ok_ii = True",
     "test_gate.py::test_gate_fails_and_voids_on_difference", "gate"),
]
FILES = {"signals": APP / "radar" / "signals.py", "score": APP / "radar" / "score.py", "profile": APP / "radar" / "profile.py",
         "gate": APP / "radar" / "gate.py",
         "search": APP / "radar" / "weights_search.py", "sweep": APP / "tools" / "sweep_d4.py",
         "cfc": APP / "tools" / "chain_fill_check.py", "s8": APP / "tools" / "s8_shapes.py",
         "conftest": APP / "tests" / "conftest.py", "guards": APP / "tests" / "test_g4_guards.py"}

import os
SKIP = set(filter(None, os.environ.get("BREAK_SKIP", "").split(",")))   # e.g. BREAK_SKIP=cfc while a sweep executes chain_fill_check.py
MUTATIONS = [m for m in MUTATIONS if (m[4] if len(m) > 4 else "signals") not in SKIP]
FILES = {k: v for k, v in FILES.items() if k not in SKIP}
# ---- mirror mode (QA structural fix 2026-09-26): mutations are applied to COPIES in a scratch mirror of app/;
# live files are never written. Proven per run by hashing every live file before and after.
import shutil
from datetime import datetime, timezone

# absolute project root (the dir holding PROJECT_BRIEF.md): a harness copy inside scratch/ must not nest its mirrors
PROJECT_ROOT = next(p for p in APP.parents if (p / "PROJECT_BRIEF.md").exists())
MIRROR_ROOT = PROJECT_ROOT / "scratch" / "developer" / "break_mirror"
LIVE_DIRS = ["radar", "tools", "tests", "config"]
LIVE_FILES = ["pyproject.toml", "uvd.sh", "uvd.ps1"]


def live_hashes():
    out = {}
    for d in LIVE_DIRS:
        for f in sorted((APP / d).rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                out[f.relative_to(APP).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    for f in LIVE_FILES:
        if (APP / f).exists():
            out[f] = hashlib.sha256((APP / f).read_bytes()).hexdigest()
    return out


def fresh_mirror():
    m = MIRROR_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for d in LIVE_DIRS:
        shutil.copytree(APP / d, m / d, ignore=shutil.ignore_patterns("__pycache__"))
    for f in LIVE_FILES:
        if (APP / f).exists():
            shutil.copy2(APP / f, m / f)
    return m


env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
before = live_hashes()
mirror = fresh_mirror()
# the venv installs this project editable; prove `import radar` resolves to the MIRROR, else the run proves nothing
where = subprocess.run([sys.executable, "-B", "-c", "import radar, pathlib; print(pathlib.Path(radar.__file__).resolve())"],
                       cwd=mirror, capture_output=True, text=True, env=env).stdout.strip()
if not where.startswith(str(mirror.resolve())):
    sys.exit(f"ABORT: in the mirror, radar imports from {where!r}, not the mirror {mirror}")
print("mirror:", mirror, "| radar imports from mirror: yes")
ok = True
for m in MUTATIONS:
    name, old, new, test = m[:4]
    key = m[4] if len(m) > 4 else "signals"
    edits = [(key, old, new)] + list(m[5] if len(m) > 5 else [])
    test = test if "::" in test else f"test_g4_signals.py::{test}"
    origs, applicable = {}, True
    for k, o, n in edits:
        rel = FILES[k].relative_to(APP)
        src = mirror / rel
        origs.setdefault(src, (APP / rel).read_bytes())
        text = src.read_bytes().decode()
        if o not in text:
            applicable = False
            break
        src.write_bytes(text.replace(o, n, 1).encode())
    if not applicable:
        for src, orig in origs.items():
            src.write_bytes(orig)
        print(f"MUTATION NOT APPLICABLE: {name}")
        ok = False
        continue
    try:
        for pc in mirror.rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)
        # own absolute basetemp inside this mirror (QA standing rule): never the shared scratch/developer/pytest-tmp
        r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "pythonpath=tests",
                            f"--basetemp={(mirror / 'pytest-tmp').resolve()}", f"tests/{test}"], cwd=mirror, capture_output=True, text=True, env=env)
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        red = r.returncode == 1 and " failed" in last   # only a real test FAILURE counts; "no tests ran"/errors are not red
        print(f"{'RED ' if red else 'NOT-RED (BAD)'} {name} -> {test}: rc={r.returncode} {last or r.stderr[-200:]}")
        ok &= red
    finally:
        for src, orig in origs.items():
            src.write_bytes(orig)   # mirror copies restored for the next mutation
after = live_hashes()
unchanged = before == after
changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
print(f"live files hashed: {len(before)} | unchanged before/after: {unchanged} {changed[:5]}")
sys.exit(0 if ok and unchanged else 1)
