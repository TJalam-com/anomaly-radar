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
    ("S6 edge-direction flag ignored", "WHERE t.direction IN ({dirs}) AND t.hop <=", "WHERE TRUE AND t.hop <=", "test_s6_in_and_out"),
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
    ("S1 truncation rule ignored", "        elif trunc:\n            # QA funding-window rule (corrected)",
     "        elif False:\n            # QA funding-window rule (corrected)", "test_s1_funding_window_truncation"),
    ("S1 truncated -> always first_trade (rejected rule)",
     "search window\n            if ftt is not None and lb_ts is not None and ftt < lb_ts:", "search window\n            if ftt is not None:",
     "test_s1_funding_window_truncation"),
    ("S1 incomplete hop-1 -> always first_trade (fallback active at bound 0)",
     "(inactive while the bound is 0).\n            if ftt is not None and lb_ts is not None and ftt < lb_ts:",
     "(inactive while the bound is 0).\n            if ftt is not None:",
     "test_s1_uses_funding_only_from_complete_hop1_in"),
    ("path_flags edge-direction ignored", "tr AS (SELECT t.* FROM transfers t JOIN prof USING (proxy_wallet) WHERE t.direction IN ({dirs}))",
     "tr AS (SELECT t.* FROM transfers t JOIN prof USING (proxy_wallet) WHERE TRUE)", "test_path_flags_respect_edge_direction"),
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
     "test_g4_weights_search.py::test_bootstrap_rng_is_constructed_with_the_given_seed", "search"),   # deterministic target (QA)
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
    # ---- r15 build (r13 Δ14, r14 Δ20/Δ21/Δ22, r15 Δ24–Δ30, QA C1–C5)
    ("path: S6 incomplete check dropped", "        elif linked == 0 and incomplete:          # a negative needs every path expansion complete (F-4); C5: wins",
     "        elif False:", "test_s6_negative_needs_complete_path"),
    ("path: missing fetch record not counted", "UNION SELECT proxy_wallet FROM missing", "",
     "test_selected_counterparty_without_fetch_record_is_incomplete"),
    ("path: to_block_ts not checked", "AND coalesce(to_block_ts, 0) >= {t}) AS complete", ") AS complete",
     "test_s6_negative_needs_complete_path"),
    ("path: hub kept in C(X)", """                   AND NOT EXISTS (SELECT 1 FROM hub h WHERE h.proxy_wallet = tr.proxy_wallet AND h.direction = tr.direction
                                   AND h.hop = tr.hop + 1 AND h.address = tr.counterparty)),""", "),",
     "test_hub_capped_counterparty_is_no_na_and_counted"),
    ("path: hub treated as an incomplete expansion", "                 FROM ex WHERE status <> 'hub'),", "                 FROM ex),",
     "test_hub_capped_counterparty_is_no_na_and_counted"),
    ("path: breadth_truncated wins over transfers_unverified",
     "        elif linked == 0 and incomplete:          # a negative needs every path expansion complete (F-4); C5: wins",
     "        elif linked == 0 and incomplete and not btrunc:", "test_precedence_unverified_over_breadth_truncated"),
    ("S1: funding from an incomplete hop-1 expansion", "        if not hop1_ok:", "        if False:",
     "test_s1_uses_funding_only_from_complete_hop1_in"),
    ("writer: sub-range contiguity dropped", 'all(b["from"] == a["to"] + 1 for a, b in zip(subs, subs[1:]))', "True",
     "test_g4_profile_events.py::test_gap_free_plants", "profile"),
    ("writer: follow by arrival order", 'key=lambda cp: (-agg[cp]["amount_raw"], agg[cp]["first_block"] or 0, agg[cp]["log_index"], cp))',
     'key=lambda cp: -agg[cp]["amount_raw"])', "test_g4_profile_events.py::test_follow_rule_independent_of_rpc_order", "profile"),
    ("writer: writer_id over profile.py only", 'if (name == "radar" or name.startswith("radar.")) and f:', 'if name == "radar.profile" and f:',
     "test_g4_profile_events.py::test_writer_id_covers_every_module_in_closure", "writer"),
    ("writer: pins accepted for any PROF id", "return {wid for prof, wid, _ in read_pins(pins_path) if prof == prof_id}",
     "return {wid for prof, wid, _ in read_pins(pins_path)}", "test_g4_profile_events.py::test_pins_parse_and_refuse", "writer"),
    ("writer: start without pin check", "    pins_sha = wid_mod.require_pinned(Path(a.pins), a.prof_id, WID)", "    pins_sha = None",
     "test_g4_profile_events.py::test_writer_refuses_to_start_unpinned", "profile"),
    ("reader: PROF-001 files accepted", "        if old:\n", "        if False:\n",
     "test_g4_score.py::test_reader_refuses_missing_writer_id_and_prof001_files", "writer"),
    ("reader: missing writer_id accepted", 'bad = [f["path"] for f in files if f.get("writer_id") not in pins]',
     'bad = [f["path"] for f in files if f.get("writer_id") not in pins | {None}]',
     "test_g4_score.py::test_reader_refuses_missing_writer_id_and_prof001_files", "writer"),
    ("reader: integrity check dropped", 'if not p.exists() or _sha(p) != f["sha256"]:', "if not p.exists():",
     "test_g4_score.py::test_reader_refuses_tampered_file", "writer"),
    ("gate: check (ii) ignored", "    ok_ii = all(v[\"equal\"] for v in check_ii.values())", "    ok_ii = True",
     "test_gate.py::test_gate_fails_and_voids_on_difference", "gate"),
]
# ---- r15 + S6 v3 fold-in (r15v3_prep FROZEN_MANIFEST e330b2dd..., Tester 1 cleared): the prep mutants, + QA N1/N2/N4
MUTATIONS += [
    ('partners counted over all profiled', 'others = (by_x[x] & natural) - {w}', 'others = by_x[x] - {w}', 'test_s6_v3.py', 'signals'),
    ('lookup set without the >=1 natural condition', 'if len(ws) >= 2 and ws & natural}', 'if len(ws) >= 2}', 'test_s6_v3.py', 'signals'),
    ('activity <= G -> < G', 'if act is not None and act <= G:', 'if act is not None and act < G:', 'test_s6_v3.py', 'signals'),
    ('incomplete lookup treated as complete', 'if r is not None and knee_g.complete_at(r, bt):', 'if r is not None:', 'test_s6_v3.py', 'signals'),
    ('activity_unverified rule removed', 'elif unverified and comp < 1.0:', 'elif False:', 'test_s6_v3.py', 'signals'),
    ('activity_unverified applied even at component 1.0', 'elif unverified and comp < 1.0:', 'elif unverified:', 'test_s6_v3.py', 'signals'),
    ('transfers_unverified applied even at component 1.0', 'elif own_incomplete and comp < 1.0:', 'elif own_incomplete:', 'test_s6_v3.py', 'signals'),
    ('F-4 literal (value + flag) for own-incomplete partial positives', 'elif own_incomplete and comp < 1.0:', 'elif own_incomplete and linked == 0:', 'test_s6_v3.py', 'signals'),
    ('precedence swapped (activity before transfers unverified)', '            elif own_incomplete and comp < 1.0:\n                res = (None, None, "transfers_unverified", {})\n            elif unverified and comp < 1.0:\n                res = (None, None, "activity_unverified", {})', '            elif unverified and comp < 1.0:\n                res = (None, None, "activity_unverified", {})\n            elif own_incomplete and comp < 1.0:\n                res = (None, None, "transfers_unverified", {})', 'test_s6_v3.py', 'signals'),
    ('edge cut block <= -> <', 'AND t.first_block <= {bt}', 'AND t.first_block < {bt}', 'test_s6_v3.py', 'signals'),
    ('edge cut ignored (SNAP edges in a replay cell)', 'AND t.first_block IS NOT NULL AND t.first_block <= {bt}', 'AND TRUE', 'test_s6_v3.py', 'signals'),
    ('graded component -> linked / 5', 'comp = graded(linked, K)', 'comp = min(1.0, linked / 5)', 'test_s6_v3.py', 'signals'),
    ('positive-on-incomplete-path counter not incremented', 'c["n_s6_positive_on_incomplete_path"] += bool(raw) and bool(ev.get("incomplete_path"))', 'c["n_s6_positive_on_incomplete_path"] += 0', 'test_s6_v3.py', 'signals'),
    ('recorded-G requirement removed', 'if "_s6_G" not in P or "_block_t" not in P or', 'if False and "_block_t" not in P or', 'test_s6_v3.py', 'signals'),
    ('as-of activity ignores block(t)', 'return sum(1 for b in fb if b <= int(block_t))', 'return len(list(fb))', 'test_s6_v3.py', 'knee_g'),
    ('capped regardless of cap_block', 'return bool(record["capped"]) and record["cap_block"] is not None and record["cap_block"] <= int(block_t)', 'return bool(record["capped"])', 'test_s6_v3.py', 'knee_g'),
    ('precomputed activity accepted (as-of rule bypassable)', 'if "first_blocks" not in record:', 'if False:', 'test_s6_v3.py', 'knee_g'),
    ('merge order: per-stream (outbound then inbound) instead of (block, log_index)', 'for k in sorted(merged):', 'for k in list(merged):', 'test_activity_lookup.py', 'activity'),
    ('self-transfer counted as a counterparty', 'if y == X or y in first:', 'if y in first:', 'test_activity_lookup.py', 'activity'),
    ('fetching continues after the cap', 'if st["capped"] or st["failed"]:', 'if st["failed"]:', 'test_activity_lookup.py', 'activity'),
    ('block(T) non-strict (ts <= T)', 'if ts(mid) < t_unix:', 'if ts(mid) <= t_unix:', 'test_activity_lookup.py', 'activity'),
    ('lookup set without the >=1 natural condition (fetch side)', 'if len(ws) >= 2 and nn >= 1:', 'if len(ws) >= 2:', 'test_activity_lookup.py', 'activity'),
    ('stop list ignored in the lookup set', '        if x in stop:\n            continue', '        pass', 'test_activity_lookup.py', 'activity'),
    ('uncapped record need not cover block(t)', 'return bool(rec["capped"]) or rec["to_block"] >= int(block_t)', 'return True', 'test_activity_lookup.py', 'knee_g'),
    ('knee: mode tie -> highest bin', 'mode = h[:TERMINAL].index(top)', 'mode = TERMINAL - 1 - h[:TERMINAL][::-1].index(top)', 'test_knee_g_v3.py', 'knee_g'),
    ('knee: drop < -> <=', 'if h[k] < DROP * h[k - 1]:', 'if h[k] <= DROP * h[k - 1]:', 'test_knee_g_v3.py', 'knee_g'),
    ('knee: terminal-bin rule removed', 'if a is None or a >= 256:', 'if a is None:', 'test_knee_g_v3.py', 'knee_g'),
    ('knee: clamp removed', 'return max(GMIN, min(GMAX, g)), mode', 'return g, mode', 'test_knee_g_v3.py', 'knee_g'),
    ('knee: population over all sharers', 'r["n_natural_sharers"] >= 2', 'r["n_natural_sharers"] >= 0', 'test_knee_g_v3.py', 'knee_g'),
    ('knee: incomplete records admitted', 'if r["complete"] and r["n_natural_sharers"]', 'if r["n_natural_sharers"]', 'test_knee_g_v3.py', 'knee_g'),
    ('knee: first_block <= -> <', 'return sum(1 for b in fb if b <= int(block_t))', 'return sum(1 for b in fb if b < int(block_t))', 'test_knee_g_v3.py', 'knee_g'),
    ('g_record: ledger membership not checked', '    if led is None:', '    if False:', 'test_g_record_v3.py', 'g_record'),
    ('g_record: lookup/transfer inputs not bound', 'if rec.get("inputs", {}).get(f) != derived.get(f) or derived.get(f) is None or sha(Path(prof) / f) != derived[f]:', 'if False:', 'test_g_record_v3.py', 'g_record'),
    ('g_record: params file not bound', 'if rec.get("params_sha256") != params_sha:', 'if False:', 'test_g_record_v3.py', 'g_record'),
    ('g_record: snap_block not bound', 'if rec.get("snap_block") != man.get("snap_block"):', 'if False:', 'test_g_record_v3.py', 'g_record'),
    ('g_record: ledger G not compared', 'if rec.get("G") != led["G"] or', 'if False or', 'test_g_record_v3.py', 'g_record'),
    ('derive_g: post-SNAP edges counted as natural sharers', 'AND first_block IS NOT NULL AND first_block <= {bt}', 'AND TRUE', 'test_g_record_v3.py', 'derive_g'),
    ('derive_g: natural filter removed', 'if w in natural and x != w:', 'if x != w:', 'test_g_record_v3.py', 'derive_g'),
    ('score: G taken without the recorded-G check', 'g_meta = g_record.check(g_record_path, g_records, Path(prof), params_sha, snap_sha)', 'g_meta = {"G": 16, "block_t": 500}', 'test_g_record_v3.py', 'score'),
    ('in-edge mode: flag ignored (all directions link)', '            if d in link_dirs:', '            if True:', 'test_s6_v3.py', 'signals'),
    ("in-edge mode: lookup set from link edges only (not the default run's set)", 'L = {x for x, ws in by_x_all.items() if len(ws) >= 2 and ws & natural}', 'L = {x for x, ws in by_x.items() if len(ws) >= 2 and ws & natural}', 'test_s6_v3.py', 'signals'),
    ('in-edge mode: G re-derived / altered', 'P["_s6_G"], P["_block_t"] = g_meta["G"], g_meta["block_t"]', 'P["_s6_G"], P["_block_t"] = (g_meta["G"] if tuple(s6_edges) == ("in", "out") else g_meta["G"] + 1), g_meta["block_t"]', 'test_g_record_v3.py', 'score'),
    ('scorer: hop >= 2 guard removed', '            if n_deep:', '            if False:', 'test_g_record_v3.py', 'score'),
    ('profile: v3 hop-1-only guard removed', 'if P.get("s6_rule") == "C1" and int(P["s6_max_hops"]) != 1:', 'if False:', 'test_profile_v3.py', 'profile'),
    ('lookup: calls not counted', '        ins_["calls"] += 1', '        pass', 'test_profile_v3.py', 'activity'),
    ('lookup: bisection cause not classified', 'ins_["splits"]["result_count" if ("result" in m or "more than" in m) else "span" if ("range" in m or "block" in m) else "other"] += 1', 'pass', 'test_profile_v3.py', 'activity'),
    ('R15V3-1: replay-scope guard removed', '    if bad:\n        raise ValueError(f"S6 v3 has no per-cell', '    if False:\n        raise ValueError(f"S6 v3 has no per-cell', 'test_s6_v3.py', 'signals'),
    ('R15V3-2: pre-fix fail-open (no t guard, t defaults to 0)', ' or "_t_asof_unix" not in P or P["_t_asof_unix"] is None:\n        raise ValueError("S6 v3 needs P[\'_s6_G\'] (recorded G), P[\'_block_t\'] (block(t)) and P[\'_t_asof_unix\'] (t; no default)")\n    G, bt, K, t = int(P["_s6_G"]), int(P["_block_t"]), float(P["s6_graded_k"]), int(P["_t_asof_unix"])', ':\n        raise ValueError("S6 v3 needs P[\'_s6_G\'] (recorded G), P[\'_block_t\'] (block(t))")\n    G, bt, K, t = int(P["_s6_G"]), int(P["_block_t"]), float(P["s6_graded_k"]), int(P.get("_t_asof_unix") or 0)', 'test_s6_v3.py', 'signals'),
    ('R15V3-2: t requirement removed', ' or "_t_asof_unix" not in P or P["_t_asof_unix"] is None:', ':', 'test_s6_v3.py', 'signals'),
    ('R15V3-5: no_transfers_found counts post-cut rows', 'AND first_block <= {bt} AND direction IN ({dl}) GROUP BY 1', 'AND direction IN ({dl}) GROUP BY 1', 'test_s6_v3.py', 'signals'),
    ('R15V3-3: SNAP not bound in the G check', 'if not snap_rec or snap_rec != man.get("snap_manifest_sha256") or snap_rec != snap_manifest_sha:', 'if False:', 'test_g_record_v3.py', 'g_record'),
    ('R15V3-4: non-default ledger still G5-eligible', 'if P.get("s6_rule") == "C1" and prof is not None and Path(g_records).resolve() != G_RECORDS.resolve():', 'if False:', 'test_g_record_v3.py', 'score'),
    ('R15V3-6: duplicate sha with different fields accepted (last wins)', 'if prev is not None and {k: prev[k] for k in e} != e:', 'if False:', 'test_g_record_v3.py', 'g_record'),
    ('R15V3-7: superseded records accepted', '    if led["superseded"]:', '    if False:', 'test_g_record_v3.py', 'g_record'),
    ('R15V3-7: superseded computed as never', 'e["superseded"] = last[e["prof_id"]] != sha_', 'e["superseded"] = False', 'test_g_record_v3.py', 'g_record'),
    ('S4 input of record not hash-checked', 'if esha != (event_times_sha or G2E_EVENT_TIMES_SHA256):', 'if False:', 'test_g_record_v3.py', 'score'),
    ('S4 pin override not marked non-G5', 'if event_times_sha and event_times_sha != G2E_EVENT_TIMES_SHA256:', 'if False:', 'test_g_record_v3.py', 'score'),
    ('S4: r3a header map removed', 'HEADER_MAP = {"event_ts_tz": "event_tz", "hedged_report_precision": "hedged_report_ts_precision"}', 'HEADER_MAP = {}', 'test_events_r3a.py', 'events'),
    ('S4: inferred zone not floored (b ignored)', 'tz, _ = _tz("UNKNOWN" if inferred else g(tz_k))', 'tz, _ = _tz(g(tz_k))', 'test_events_r3a.py', 'events'),
    ('S4: unknown columns accepted', '    if unknown:\n        raise EventFileError(f"event file has unknown columns', '    if False:\n        raise EventFileError(f"event file has unknown columns', 'test_events_r3a.py', 'events'),
    ('S4: two headers mapping to one role accepted', '        if c in canon:\n', '        if False:\n', 'test_events_r3a.py', 'events'),
    ('S4: non-boolean inferred flag read as false', '    raise EventFileError(f"column {col}: {s!r} is not a boolean; S4 refuses")', '    return False', 'test_events_r3a.py', 'events'),
    ('S4: missing required column accepted', '    if missing:\n        raise EventFileError(f"event file lacks columns', '    if False:\n        raise EventFileError(f"event file lacks columns', 'test_events_r3a.py', 'events'),
    ('tz: NAME(UTC+hh:mm) form not parsed (pre-fix)', 'm = re.fullmatch(r"(?:[A-Z]{2,5}\\s*\\()?\\s*(?:UTC|GMT)?', 'm = re.fullmatch(r"\\s*(?:UTC|GMT)?', 'test_events_r3a.py', 'events'),
    ('tz: out-of-range offset accepted', '    if h > 14 or mm >= 60:\n        return unknown', '    if False:\n        return unknown', 'test_events_r3a.py', 'events'),
    ('path_flags: t fail-open default restored', '    if P.get("_t_asof_unix") is None:     # QA: no fail-open default (t = 0 made every to_block_ts >= t trivially true)\n        raise ValueError("path_flags needs P[\'_t_asof_unix\'] (the as-of instant t)")\n    t = int(P["_t_asof_unix"])\n', '    t = int(P.get("_t_asof_unix") or 0)\n', 'test_s6_v3.py', 'signals'),
    ('tz (real r3a): old parser restored (strip UTC, fail on the label)', '    m = re.fullmatch(r"(?:[A-Z]{2,5}\\s*\\()?\\s*(?:UTC|GMT)?\\s*([+-])(\\d{1,2})(?::?(\\d{2}))?\\s*\\)?", raw)', '    m = re.fullmatch(r"\\s*(?:UTC|GMT)?\\s*([+-])(\\d{1,2})(?::?(\\d{2}))?\\s*", raw)', 'test_events_r3a.py::test_real_r3a_stated_irst_anchors_are_0430z_and_known', 'events'),
    ('in-edge mode: no_transfers_found counts all directions', 'AND first_block <= {bt} AND direction IN ({dl}) GROUP BY 1', 'AND first_block <= {bt} GROUP BY 1', 'test_s6_v3.py', 'signals'),
    ('in-edge mode: own record checked in both directions', 'own_incomplete = not all(e.get(d) for d in link_dirs)', 'own_incomplete = not (e.get("in") and e.get("out"))', 'test_s6_v3.py', 'signals'),
    ('finalize: lookup files left out of the manifest', 'for f in sorted((prof_dir / "lookups").glob("*.json")) if (prof_dir / "lookups").exists() else []:', 'for f in []:', 'test_profile_v3.py', 'profile'),
    ('run_lookups: not resumable (re-fetches existing records)', '        if target.exists():\n            continue', '        pass', 'test_profile_v3.py', 'activity'),
    ('N1: SNAP without finished_at gives t = 0', '    if not fin:        # QA N1', '    if False:        # QA N1', 'test_g4_score.py::test_scorer_refuses_a_snap_without_finished_at', 'score'),
    ('N2: empty inferred flag next to a timestamp read as false', '            if g(ts_k) and not g(inf_k):', '            if False:', 'test_events_r3a.py::test_empty_inferred_flag_next_to_a_timestamp_is_refused', 'events'),
    ('N4: projection uses p50 instead of the mean', '    mean = (sum(allc) / len(allc)) if allc else None', '    mean = out["calls_per_lookup_all"]["p50"]', 'test_profile_v3.py::test_dry_run_projection_uses_mean_with_p90_upper_and_p50', 'report'),
    ('N4: quantile index floored (p90 not an upper bound)', 'math.ceil(f * (len(v) - 1))', 'int(f * (len(v) - 1))', 'test_profile_v3.py::test_dry_run_projection_uses_mean_with_p90_upper_and_p50', 'report'),
]
FILES = {"signals": APP / "radar" / "signals.py", "score": APP / "radar" / "score.py", "profile": APP / "radar" / "profile.py",
         "gate": APP / "radar" / "gate.py", "writer": APP / "radar" / "writer_id.py",
         "search": APP / "radar" / "weights_search.py", "sweep": APP / "tools" / "sweep_d4.py",
         "cfc": APP / "tools" / "chain_fill_check.py", "s8": APP / "tools" / "s8_shapes.py",
         "conftest": APP / "tests" / "conftest.py", "guards": APP / "tests" / "test_g4_guards.py",
         "knee_g": APP / "radar" / "knee_g.py", "activity": APP / "radar" / "activity.py", "g_record": APP / "radar" / "g_record.py",
         "events": APP / "radar" / "events.py", "derive_g": APP / "tools" / "derive_g.py", "report": APP / "tools" / "dryrun_lookup_report.py"}

import os
SKIP = set(filter(None, os.environ.get("BREAK_SKIP", "").split(",")))
REPEAT = max(1, int(os.environ.get("BREAK_REPEAT", "3")))   # QA evidence rule: N = 3 by default   # QA: run each mutant N times; mixed outcomes = FLIP = BAD (probabilistic)   # e.g. BREAK_SKIP=cfc while a sweep executes chain_fill_check.py
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
    test = test if ("::" in test or test.endswith(".py")) else f"test_g4_signals.py::{test}"   # a bare file = the whole file
    origs, applicable = {}, True
    for k, o, n in edits:
        rel = FILES[k].relative_to(APP)
        src = mirror / rel
        origs.setdefault(src, (APP / rel).read_bytes())
        text = src.read_bytes().decode()
        if text.count(o) != 1:   # 0 = anchor gone; >1 = ambiguous (would mutate the first match, maybe the wrong site) -> BAD
            applicable = False
            print(f"anchor occurs {text.count(o)}x in {rel}")
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
        reds, last, rc = [], "", None
        for _rep in range(REPEAT):
            r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "pythonpath=tests",
                                f"--basetemp={(mirror / 'pytest-tmp').resolve()}", f"tests/{test}"], cwd=mirror, capture_output=True, text=True, env=env)
            last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
            rc = r.returncode
            reds.append(r.returncode == 1 and " failed" in last)   # only a real test FAILURE counts; "no tests ran"/errors are not red
        red = all(reds)
        tag = "RED " if red else ("FLIP (BAD, probabilistic)" if any(reds) else "NOT-RED (BAD)")
        rep = f" [{sum(reds)}/{REPEAT} red]" if REPEAT > 1 else ""
        print(f"{tag} {name} -> {test}: rc={rc} {last}{rep}")
        ok &= red
    finally:
        for src, orig in origs.items():
            src.write_bytes(orig)   # mirror copies restored for the next mutation
after = live_hashes()
unchanged = before == after
changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
print(f"live files hashed: {len(before)} | unchanged before/after: {unchanged} {changed[:5]}")
sys.exit(0 if ok and unchanged else 1)
