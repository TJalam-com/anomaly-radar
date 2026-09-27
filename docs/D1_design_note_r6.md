# D1 design note r6: delta on r4 + r5 (F3 bootstrap, QA fields A1–A11, run modes, D-b/D-c results, step 4)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26.

**Bases:**
- r4: `e59ddcea2b47ec21ff39c33ebaf821b902425766fee77335b8a4df4d5fe8d9ab` (19453 B)
- r5: `c4df6ec9ca06b807292cc9243f765d9e0536b490a2d56ad74e5f614613e8a4b8` (10194 B)

Per QA, r4+r5+r6 are cleared together at r6's hash, pending Tester 1's field review. r4 and r5 are not edited; where they conflict with r6, r6 wins.

## Δ1. Weights selection: bootstrap replaces the +0.02 margin (F3, replaces r5 Δ3 step 5)

Steps 1–4 and 6–7 of r5 Δ3 stand. Step 5 becomes:

5. For each grid config W ≠ W0, run a **paired bootstrap over the look-half controls**:
   - `B = 2000` resamples, fixed seed `20260926`, recorded.
   - Each resample draws, with replacement and in equal number to the look half, **positive units** (cluster units: all positive wallets of one unit move together) and **negative units** (single wallets). These are the same unit rules as the split. The unit id is read from the look file's unit column, whose name is taken from the file header, not assumed. This is the only per-row field used besides the label and the wallet.
   - In each resample, compute `ΔAUC = AUC(W) − AUC(W0)` on the same resampled controls (scorer-only mode).
   - **Select W only if the 5th percentile of ΔAUC > 0** (90% two-sided lower bound).
   - If several configs qualify, take the one with the highest 5th percentile. Ties go to the smallest L1 distance to W0. If none qualifies, keep **W0**.
   - Every config's bootstrap is logged to the weights-search JSONL before selection: B, seed, mean ΔAUC, 5th/50th/95th percentiles, n_pos_units, n_neg_units.
   - With ~12 look positives (≈6 in M), W0 is the expected outcome. The procedure is designed so noise cannot beat it.

## Δ2. Bypass list as opaque input (A5, replaces `is_control` everywhere)

- **Removed:** `wallets.is_control`, "controls that would have failed" counts, and any code path that names, derives or labels controls.
- **Added:** `--bypass-list <path>` names one externally supplied file.
  - The code reads only a wallet-address column. The column name comes from the file header, the first column matching `/wallet|address/i`, else the run refuses.
  - `runs.bypass_list_path`, `runs.bypass_list_sha256` and `runs.bypass_list_rows` are recorded.
  - The file is not opened by the Developer beyond what the code reads. Rows are lower-cased and deduplicated.
- **Per wallet:** `bypass_listed` BOOLEAN. `profiled = passed_prefilter OR bypass_listed`.
- **Weights search (r5 Δ3):** the look file (labels) is a different input from the bypass list. The code keeps them apart: the search reads the look file, the pipeline reads the bypass list. Neither is derived from the other.

## Δ3. Per-wallet, per-scope completeness (A1–A3, A9)

```sql
universe(run_id, scope, proxy_wallet, passed_prefilter BOOLEAN, profiled BOOLEAN, bypass_listed BOOLEAN,
         PRIMARY KEY (run_id, scope, proxy_wallet))
```

- **A1:** every wallet in `U(scope)` gets a `universe` row, including non-profiled wallets.
- **A2:** `scores` has **exactly one row per wallet in U(scope)** for scope `'all'` (and `'all_ext'`), non-profiled included. A non-profiled wallet's total comes from its computable signals (S3, S4, S5, S7). S1/S2/S6/S8 are NA `'not_profiled'`.
- **A3:** `signals` has a row for **every** `(wallet ∈ U, scope, S1..S8)`. Each row carries `component`, `raw_value` and `na_reason` (NA rows have NULL raw/component).
- Scopes `condition` and `event:<id>` are also dense over their own U.
- **Size:** |U(all)| = 155,308 wallets (SNAP-003, `all` walk). 'all' alone gives 155,308 score rows and 1,242,464 signal rows. The condition scope is Σ_c |U(c)| (computed at build). Storage is DuckDB + parquet per run, measured at build.
- **A9:** `'all_ext'` is computed from the **M_ext snapshot only**, with its own U_ext (wallets with ≥1 fill in M_ext conditions), its own prefilter evaluation within the M_ext scope set, and the same A1–A3 fields. It never reads SNAP-003 rows.
- **Tests:**
  - `count(scores WHERE scope='all') = |U(all)|`, where |U| is computed independently: a separate DuckDB query on `trades.parquet` that does not use scorer code. Break: drop one non-profiled wallet → red.
  - `count(signals WHERE scope='all') = 8·|U(all)|`. Break: skip S7 for one wallet → red.

## Δ4. Runs store applied values, not only hashes (A4, A8, A7, A10)

```sql
runs(... existing ...,
     weights_applied_json TEXT,    -- {"S1":w1,...,"S8":w8} after normalisation, plus "pre_norm_sum"
     params_json TEXT,             -- every P.* value applied (already r5), plus perturbation block (Δ5)
     snapshots_json TEXT,          -- [{"snap":"SNAP-003","manifest_sha256":"9a901db6…"},{"step4_manifest_sha256":"4298ee2f…"}]
     event_times_path TEXT, event_times_sha256 TEXT,     -- A7: results/G2E_event_times_2026-09-26.csv (Tester 2)
     bypass_list_path TEXT, bypass_list_sha256 TEXT, bypass_list_rows INT)
```

- **A4 test:** recompute every `total` as Σ `weights_applied[i]·component_i` (non-NA) from the stored rows and assert equality (1e-12). Break: alter one stored weight → red.
- **A7:** S4 `event_ts` comes from `results/G2E_event_times_2026-09-26.csv` by path + sha256, with its precision column (r5 Δ1). This replaces "Planner table" in r4.
- **A8:** SNAP-G3 = SNAP-003 manifest `9a901db6c19e3ccfd4ced80d46d48673a7fd2a4d4d2b530396a7ebb934b83979`. Step-4 side-car manifest = `4298ee2f…` (Δ7).
- **A10 pre-run assert:** the scoring view refuses to build if any in-scope condition has NULL chain `resolution_ts`. **Implemented and break-tested** in `radar/scoring_view.py` (Δ7).

## Δ5. Run modes (A6 i–iii, extends r5 Δ4)

Every mode gets a separate `run_id` and never overwrites another run. Each mode's parameters are in `runs.params_json` / `runs.override_json`.

| mode | CLI | effect |
|---|---|---|
| (i) weight zeroed | `--override S1=0 --tag s1_zeroed` (any signal) | r5 Δ4 |
| (ii) timestamp shuffle | `--shuffle-ts --shuffle-seed <n> --tag shuffle_<n>` | Within each condition, the `ts` values of `signal_fills` rows are permuted among that condition's rows by the recorded seed. Rows keep wallet, side, token, size and price. `resolution_ts` and the scope are unchanged. Removes timing information and keeps everything else. `params_json.perturbation = {"type":"shuffle_ts","seed":n,"unit":"condition"}` |
| (iii) S6 outbound off | `--s6-edges in --tag s6_in_only` | S6 uses inbound edges only. Reason: the outbound edge type was motivated by a control-derived fact (r5 Δ0, QA H-001b), so reports show results with it off. `params_json.s6_edges = "in"` |

Tests:
- (ii) Plant a coordinated S7 cluster (4 wallets within 5 min). The normal run scores it; the shuffle run with a fixed seed breaks the timing, so S7 drops, and the non-timing S3 is unchanged. Break: shuffle across conditions instead of within → the S3 assert changes → red.
- (iii) Plant an outbound-only link (2 wallets, same destination, no shared funder). The normal run gives S6 > 0; the `s6_in_only` run gives 0. Break: ignore the flag → red.
- All modes: the base run's rows are byte-identical before and after each mode run (r5 Δ4 isolation test).

## Δ6. D-b and D-c results (measured today), and consequences

### D-b: auto-redeem separability (partial)
- 6 REDEEM rows of 6 winners of the control market, 2026-09-26 ~13:55–14:00Z.
- **Activity rows:** no field distinguishes an automatic from a user redemption. `REDEEM` rows carry `price 0`, empty `token_id` and `side`, and `size = usdc_size`.
- **On chain:** all 6 redemption txs were user-signed shapes:
  - 4 × Gnosis-Safe `execTransaction` (selector `0x6a761202`) called on the wallet itself, from 4 distinct EOAs
  - 2 × `relayCall` (selector `0x405cec67`) on `0xd216153c06e857cd7f72665e0af1d7d82172f494` = **"Polymarket: Relay Hub"** (Polygonscan label, verified RelayHub). This is a GSN meta-tx carrying the user's signature.
- No platform-only redemption path was seen. Six is too few to rule one out, and the Data API still lists `auto_redeem_*` ingestion sources.
- **Consequence (proposal):** S8 `quick` counts only redemptions whose tx shape is one of the two user-signed shapes above. Any other shape → S8 NA `'auto_redeem_unknown'` for that wallet-condition. `dormant` is unaffected. The shape check needs 1 `eth_getTransactionByHash` per REDEEM of a winning bet. Before build, measure the shape distribution on ≥200 REDEEMs across ≥10 markets; if any third shape exists, report it before coding.
- **Registry by-products** (Polygonscan labels, to seed `entities`, `verified_on_polygon=TRUE` from label):
  - `0x3a3bd7bb9528e159577f7c2e685cc81a765002e2` "Polymarket: Wrapped Collateral" (kind `wrapper`)
  - `0xd91e80cf2e7be2e162c6513ced06f1dd0da35296` "Polymarket: Neg Risk Adapter" (kind `polymarket_infra`; confirms the G1 oracle label)
  - `0xd216153c…f494` "Polymarket: Relay Hub" (kind `polymarket_infra`)

### D-c: prefilter size and call budget
- SNAP-003 (`all` walk, `signal_fills` via the G1 resolution times), 2026-09-26 ~13:52Z. |U(all)| = **155,308**.

| rule (p_low = 0.20) | stake ≥ 500 | ≥ 1,000 | ≥ 5,000 |
|---|---|---|---|
| single leg (r4 wording) | 2,059 | 1,025 | 147 |
| Σ per (wallet, condition) | 3,004 | 1,682 | 393 |
| **Σ over scope 'all' (= S3 raw)** | 4,040 | **2,414** | 617 |

  p_low 0.15 / 0.25 at 1,000, single-leg: 675 / 1,425.
- **Rule change (proposal):** the prefilter = **S3 raw over the scope ≥ `pre_min_stake`**, i.e. the same Σ as S3. The single-leg wording in r4 would drop 1,389 wallets that S3 scores > 0 (split orders), so a wallet could score on S3 yet never be profiled. At `pre_min_stake = 1,000`: **2,414 profiled + bypass-listed**. QA rules the value.
- **Activity walks are too heavy:** 20 random passed wallets (seed 11) have a median of 888 activity rows, but the max is 1,254,239 rows (1,255 pages). The 20 took 1,007 s; the extrapolated full walks for 2,414 wallets are ~34 h. **Replace full walks with targeted calls:**

| need | call | measured |
|---|---|---|
| S1 `first_trade_ts` | `v2/activity?user=&limit=1&sort_direction=ASC` | earliest row returned (tested). `sortDirection` also accepted; `sort=asc` is **silently ignored** (returns newest). The code asserts that the ASC result ts ≤ the DESC result ts |
| S2 denominator | `v2/user-stats` → `all_time_pnl.volume_usdc` | = Σ activity TRADE `usdc_size` over **both sides**, ratio 0.9926–1.0011 on 6 wallets |
| S8 redeem | `v2/activity?user=&condition=&type=REDEEM` | returns REDEEM rows (tested on 6) |
| S8 dormancy | `v2/activity?user=&type=TRADE&start=<redeem_ts>&limit=1` | `start` honoured (0 rows past the last activity) |
| S6 in / out | tenderly `eth_getLogs` over the 3 collateral tokens, `to`/`from` = w, blocks 75M..latest | one call per direction per hop: 30M blocks in ~3 s (tested). **quiknode public caps at 10k blocks and cannot serve this.** No fallback for S6 until Dune |

- **S2 changes accordingly:** `raw = Σ |stake| of the wallet's legs in scope (BUY and SELL, all walk) / user-stats volume_usdc`. Both sides are counted in numerator and denominator.
- **Budget:** per profiled wallet ≈ 2 (activity ASC + user-stats) + 2 × (winning conditions) + 2 × (hops ≤ 3, fan-out bounded by the stop-list) + 1 per REDEEM (tx shape). That is ~6–15 calls; 2,414 wallets ≈ 15–36k calls, **~2–5 h** at ~0.4–0.5 s sequential. The rate limit was not measured at this volume; the first 500 calls are monitored for 429 and the run stops on the first one.

## Δ7. Step 4: done (QA blocker cleared)

- `radar/step4.py` ran 2026-09-26 14:07:33–14:10:18Z on SNAP-003 and wrote **only** `SNAP-003/step4/`. The SNAP-003 manifest is `9a901db6…` before and after.
- Per condition: one `eth_getLogs` (ConditionResolution, topic1 = conditionId, blocks 75M..latest) + one block header. 342 calls; raw responses are saved and hashed.
- **Result:**
  - 171/171 resolved
  - gate S4-ONE (exactly 1 log) passed for all
  - U-PAYOUT (chain winner vs Gamma outcome) 171/171
  - X-G1 (tx/block/ts vs `results/G1_A21_P2_2026-09-26.csv` `7cff5c1b…cc85`) 171/171
  - void = only `0x6f01b773…` (the 50/50)
  - `closed_at_delta_s ≠ 0` only for `0xd054d5a2…` (+136 s)
  - `t_ref` non-NULL 171/171
- **Side-car manifest:** `SNAP-003/step4/manifest.json` 125,827 B `4298ee2fb9cbdb6ce0dc2399afe82539d9b081789ef9678c19ebdffb8f6392fb`. It records the SNAP-003 manifest hash and the cross-check file hash.
- **Independent recount** (separate inline script, no `radar` import except the view assert): 342/342 raw files re-hash, 0 unlisted/missing, parquet hash matches, 0 differences vs the G1 csv, scoring view builds for all 171.
- **Tests** (`tests/test_step4.py`, 6):
  - resolve + void + t_ref (taker, strict `<`)
  - S4-ONE abort on 0 / 2 logs
  - never-overwrite
  - view refuses without a side-car
  - view refuses when a condition lacks resolution
  - `signal_fills` strict `<`
  - Break-tested four ways, each red, then restored byte-identical: S4-ONE off, refuse-assert off, `<` → `<=`, `t_ref` from the all walk with `<=`. The full suite is 21 passed.

## Δ8. A11 replay (noted, not blocking)

- Scope `replay:<h>:<condition_id>`: T_cut per cell recorded.
- **Every** input is filtered `ts < T_cut`: fills in all markets, funding edges, activity, the prefilter, S2's denominator (user-stats cannot be time-cut, so replay S2 uses activity sums instead), and entity labels (`verified_at < T_cut`).
- Scores for every wallet in U(m, h).
- Designed at the replay task. Nothing is built for it now.

## Δ9. Decisions for QA

| id | item |
|---|---|
| Q1 | prefilter = S3-consistent Σ rule (Δ6); `pre_min_stake` value (proposal 1,000 → 2,414 profiled) |
| Q2 | S8 `quick` limited to the two user-signed tx shapes, else NA `'auto_redeem_unknown'`; ≥200-REDEEM shape survey before build |
| Q3 | S2 both-sides numerator/denominator via user-stats (Δ6) |
| Q4 | S6 has no RPC fallback (quiknode 10k-block cap): if tenderly degrades, S6 = NA `'rpc_unavailable'` until Dune |
