# D1 design note r8: delta on r7 (run-dir contract + rulings folded in since r7)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26.

**Base:** r4+r5+r6+r7, cleared together (r6 `a149c645…4350`, r7 `62ad7d8d…7d3b`). Those files are not edited. This delta records decisions and QA rulings made since r7, and the run-directory contract QA asked for.

## Δ1. Run-directory contract v1 (QA field requirement; schema = r6 A1–A4)

Each score run writes `<runs_dir>/<run_id>/`, never overwritten. `runs_dir` is scratch until QA clears the first W0 review run.

| file | content |
|---|---|
| `run.json` | See keys below |
| `universe.parquet` | `scope, proxy_wallet, passed_prefilter, bypass_listed, profiled`: every wallet in U(scope) (A1) |
| `signals.parquet` | `scope, proxy_wallet, signal_id, raw_value, component, na_reason, evidence_json`: every (wallet ∈ U, scope, S1..S8) (A3). CHECK: raw NULL ⇔ na_reason NOT NULL |
| `scores.parquet` | `scope, proxy_wallet, total_scorer, total_pipeline, n_signals_na`: one row per wallet in U(scope) (A2). `total_pipeline` is NULL when `passed_prefilter = FALSE` |
| `trades_summary.parquet` | `scope, walk, fills_any_ts, fills_before_resolution, size_shares_any_ts` over the scope's M markets, walk-labelled (`taker` = each fill once, `all` = taker + maker legs) |

`run.json` keys (enforced by `tests/test_g4_score.py::test_run_dir_contract`):

| key | content |
|---|---|
| `contract` | `"run-dir v1 (design note r8)"` |
| `run_id`, `tag`, `base_run_id` | |
| `weights_sha256` | For an override run, this is the hash of the derived config (r5 Δ4) |
| `weights_file_sha256` | |
| `weights_applied_json` | S1..S8 after normalisation, plus `pre_norm_sum` (A4) |
| `params_file_sha256` | The frozen params file (`467a62fb…836a`) |
| `params_json` | Every applied P, `user_signed_redeem_shapes`, `perturbation`, `s6_edges` |
| `snapshots_json` | `[{id, manifest_sha256}]` for the SNAP and its step4 side-car (A8) |
| `profile` | `{path, manifest_sha256}` |
| `event_times_sha256`, `bypass_list_sha256` | NULL when not supplied (A7, A5) |
| `prefilter_rule`, `prefilter_cut`, `scope_limit_text` | |
| `mode` | `{shuffle_seed, s6_edges, override}` (A6) |
| `computable_signals` | |
| `derived` | Per-file rows + sha256 |

A4 check: every run recomputes Σ `weights_applied`·component from the stored rows and refuses to finish on any difference > 1e-12.

## Δ2. Rulings folded in since r7

| # | ruling / decision | where enforced |
|---|---|---|
| a | **Frozen params:** `config/params_frozen_2026-09-26.toml` `467a62fb…836a` (+ `.lock`). Every run records its sha256; score/profile refuse on a lock mismatch. New P from the freeze: `s6_fanout_max = 20`, `s6_hop_breadth = 3`. [fetch] caps: hop-1 > 50,000 logs → S6 NA `transfers_unavailable`; hop ≥ 2 > 5,000 logs → hub, not followed | `radar/score.py load_params`, `radar/profile.py FETCH` |
| b | **r7 precision rule is `≥`** (anchor precision ≥ lead → NA), per r7's own day-vs-24 h example; QA confirmed | `radar/events.py` |
| c | **Funding search lower bound = genesis (block 0)**, not 75M (cost measured equal). QA funding-window rule (corrected): truncated = earliest non-infra inbound within 1M blocks of the bound, OR no non-infra inbound while trades exist → if `first_trade_ts` < bound time, S1 uses `first_trade_ts`, else NA `funding_window_truncated`. S6 evidence gets `edges_possibly_incomplete` | `radar/profile.py bundle_to_rows`, `radar/signals.py s1/s6` |
| d | **S8 user-signed shapes** = `safe_exec`, `direct_eoa` (tx.from = wallet, to ∈ {CTF, NegRiskAdapter}). ERC-4337 → NA. `relay_hub` → NA unless ≥ 30 decoded samples show the owner's signature. A NegRisk REDEEM survey (≥ 50) is required before S8 is frozen | params [fetch] `user_signed_redeem_shapes` |
| e | **Stop-list:** Polymarket infra (label-verified 2026-09-26) + pselamy seed vendored unmodified (body = upstream blob `de4e3701…`, MIT header kept, all `verified_on_polygon = FALSE`) | `radar/entities.py`, `radar/vendor/` |
| f | **F6 closure:** stake/exposure from positions or chain, never holders. Bubble graph rebuilt from trades at T_ref | `tests/test_g4_guards.py` (no holders reads in signal code) |
| g | **Weight search:** pre-registered r6 Δ1 bootstrap; re-runnable end-to-end from (snapshot + frozen params + look sha). Output headed PROVISIONAL until G-HO-2 clears. Not run until QA clears the first W0 review run | `radar/weights_search.py` |
| h | **G-HO-2** (source-independent full fill count) passes by EITHER (a) one Dune aggregate query after the billing reset, OR (d′) the SAMPLED D4 sweep over all 171 markets. (d′) needs: pooled uniform p95 ≤ 1.0% (n ≥ 299) with 0 mismatches; 0-uniform markets retried at 40 uniform windows; "not exercised" markets < 5% of M taker size. NegRisk mapping calibrated on 2 markets first. The public Goldsky subgraph is deprecated ("stale and incorrect") and never used | `tools/sweep_d4.py target_met`, `tools/chain_fill_check.py` (exchange by neg_risk) |
| i | **Profiling:** 3 workers max, cross-wallet hop ≥ 2 cache, atomic bundle writes, stop on first 429. Bypass wallets are added to the same PROF dir when H-002 lands | `radar/profile.py` |
| j | **Line endings:** all app files are LF. The CRLF↔LF equivalence is in `docs/HASH_MAP_CRLF_LF_2026-09-26.md` (`8fc74773…dbd9`, QA-verified) | — |
| k | **D-007:** D: only, enforced by `tests/test_env_on_d.py`; uv only via `uvd.sh`/`uvd.ps1` | — |

## Δ3. Open

- P11: S4 NA when G2E's "stored as unit start" contract is unconfirmed. Waits for the G2E r3 file (column unknown).
- S8 freeze: waits for the NegRisk survey and the relay_hub decode.
- M_ext (`scope_set = 'ext'`, own snapshot, own U_ext): waits for the file.
