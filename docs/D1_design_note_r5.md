# D1 design note r5: delta on r4 (QA conditions F1–F5 + decisions)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26.

**Base:** `app/docs/D1_design_note_r4.md`, 19453 B `e59ddcea2b47ec21ff39c33ebaf821b902425766fee77335b8a4df4d5fe8d9ab`. QA cleared it WITH CONDITIONS. r4 stands except where changed below. r4 is not edited.

## Δ0. Decisions recorded (QA)

- **D-a** accepted: no S4 fallback.
- **D-b** accepted: measure auto-redeem separability first.
- **D-c** accepted: measure |passed| and the call budget; QA rules the values.
- **D-d** accepted: all `P.*` frozen at config hash before the first score.
- **Event table:** owned by Tester 2, `results/G2E_event_times_2026-09-26.csv`. It has a timestamp-precision column (see Δ1-S4).

**Blinding disclosure:** QA's r4 review quoted one fact from `results/T1_controls_positive_2026-09-26.md`: a published source linked a positive-control cluster by a "shared Binance deposit". The Developer has now seen that one fact, and nothing else from the control files. F2 below is a standard linkage type and is designed generically, not tuned to that case. This disclosure also goes into the G5 report via QA.

## Δ1. Signal changes

### S3 low-odds size: floor at a stake threshold (F1)
- Replaces r4's `logcap(raw, cap)`, which gave a 10 USDC ticket 0.21 and 100 USDC 0.40.
- **Component:** `floorlog(raw, P.s3_min_stake, P.s3_cap_usdc)`, where `floorlog(x, m, c) = 0` if `x < m`, else `clip01( log10(x/m) / log10(c/m) )`.
- Proposal: `s3_min_stake = 1000` (= `pre_min_stake`), `s3_cap_usdc = 100000`. So 1,000 USDC → 0, 10,000 → 0.5, 100,000 → 1.
- Values: 10 USDC → 0, 100 USDC → 0, 999 → 0.
- A stake of exactly `m` scores 0. The first positive value is just above m. This is deliberate: the threshold is where the scale starts, not a step up.
- `raw` is unchanged: Σ stake of BUYs with `price < p_low`.
- **Planted test:**
  - 50,000 shares @0.10 (5,000 USDC) → `log10(5)/log10(100)` = 0.349
  - 9,000 shares @0.10 (900 USDC) → 0
  - 10 USDC @0.05 → 0
  - the 5,000 USDC BUY with `ts = resolution_ts` → 0 (R3)

### S4 pre-event timing: same floor, plus a precision rule
- **Component:** `raw × floorlog(stake_in_window, P.s4_min_stake, P.s4_cap_usdc)`. Proposal: min 1,000, cap 50,000. This replaces r4's `logcap` for the same reason as F1.
- **New NA:** `'event_ts_too_coarse'` when the event table's precision for c is coarser than `P.s4_lead_h`. For example, day precision is coarser than a 24 h lead. The precision column is compared in seconds: S4 is NA if `precision_s > s4_lead_h·3600`.
- **Planted test (added):** set the fixture event row's precision to `day` (86400 s) with lead 24 h → NA `'event_ts_too_coarse'`. Set it to `minute` → computed.

### S6 linked wallets: inbound funders AND outbound destinations (F2)

Renamed from "shared funder" to **"shared funder / shared destination"**. The brief's S6 wording already covers "same CEX withdrawal pattern".

- **Edge types:**
  - `in`: collateral Transfer **into** w (as r4)
  - `out`: collateral Transfer **from** w to an address D
  - Tokens are USDC.e, USDC and pUSD for both.
- **Linkage:** `A(w) = F_in(w) ∪ F_out(w)`, both taken within `P.s6_max_hops` and with the **same stop-list**. Two wallets are linked if `A(w1) ∩ A(w2) ≠ ∅`.
  - An `out` hop 1 is a direct withdrawal target.
  - An `out` hop 2 follows D's own outflows. This usually reaches an exchange hot wallet, which is stop-listed, so the walk ends there.
- **Why a destination links owners:** a centralised exchange gives each user a unique deposit address, which later sweeps to the hot wallet. Two proxy wallets withdrawing to the same deposit address are therefore likely the same exchange account. The hot wallet itself is shared by everyone, so it must stay stop-listed. The high-fan-out rule from G2 applies to `out` destinations too: an address that receives from > threshold distinct wallets is treated as shared infrastructure, not a deposit address.
- **Formula:** `raw` = number of other profiled wallets in `U(scope)` linked to w (by either edge type). `evidence_json` lists the shared addresses and edge types.
- **Component:** `clip01(raw / P.s6_full_at)`, unchanged.
- **NA:**
  - `'not_profiled'`
  - `'no_transfers_found'`: no in and no out transfer located. This replaces `'no_funding_found'`.
- **Schema:** `funding_edges` gains `direction TEXT CHECK (direction IN ('in','out'))`. The key stays `(tx_hash, log_index)`, since one transfer log is one edge; direction is relative to the profiled wallet, so the view carries (wallet, direction).
- **Cost note:** outbound needs `eth_getLogs` with topic1 = w (the `from` field) in addition to topic2 = w. That doubles the per-wallet log search. It is measured in D-c.
- **Planted tests:**
  - (in) 3 wallets funded by one fresh EOA → raw 2 each → 0.4. Make the funder a listed CEX hot wallet → 0.
  - (out) 2 wallets each withdrawing to the same fixture address D (not listed, fan-out 2) → raw 1 each → 0.2. Add D to the stop-list → 0. Give D 50 distinct senders (above the fan-out threshold) → 0.
  - No transfers of either direction → NA `'no_transfers_found'`.

## Δ2. Ranking universe and control status (F5)

- **Controls are profiled and scored whether or not they pass the prefilter** (R9). Their component and total scores are computed and stored.
- **Primary counts and ranks use pipeline status.** "How many flagged", "rank among U", and precision are computed over the pipeline population. A control that would have failed the prefilter is reported with `passed_prefilter = FALSE`. In pipeline mode it counts as **not flagged**, because the production pipeline would never have scored it.
- The scorer-only view (score regardless of prefilter) is reported beside it as a second column, never merged. See Δ4.

## Δ3. Weights procedure (F3): pre-registered here, before any look-half score is seen

1. **Input:** only `results/G2_split_look_2026-09-26.csv` (re-hash before reading; expected `72912c92…721d`). The held-out half is never read, never derived by subtraction, never an input.
2. **Baseline config W0 = equal weights** over the signals that are computable on SNAP-003 (not NA for every wallet). Any signal that is NA for all wallets, e.g. S4 until the event table lands, gets weight 0, and this is recorded. Weights are normalised to sum 1.
3. **Grid (fixed now, 1 + 2m configs, where m = number of computable signals):**
   - W0
   - for each computable signal i: `W0 with weight_i = 0` (leave-one-out)
   - for each computable signal i: `W0 with weight_i doubled`, then renormalised
4. **Objective, computed on the look half only:** AUC over scope `'all'` = probability that a look-positive control outranks a look-negative control (ties count ½).
   - The **selection objective is the scorer-only AUC** (`total_scorer`), because the weights should rank what the scorer sees.
   - The **pipeline AUC** is reported beside every config and is never used for selection. In it, a control with `passed_prefilter = FALSE` ranks below every scored wallet.
5. **Selection:** pick the grid config with the highest scorer-only AUC, **only if** it beats W0 by ≥ 0.02. Otherwise W0 is kept. A tie is broken by the smallest distance to W0.
6. **Logging:** every tried config is written to `app/data/weights_search/<run_id>.jsonl` with its weights, params hash, AUC (both modes), n_pos, n_neg and look-file hash, before selection. The chosen config is written to `config/weights.toml` and hashed. The log file hash is sent to QA with the weights.
7. **No other adjustment.** After seeing scores, changing weights or `P.*` outside this grid is not allowed. A needed change goes back to QA as a new pre-registration with a new run id. `P.*` thresholds are frozen per D-d **before** step 2 and are not part of the grid.

## Δ4. Run modes and overrides (F4)

CLI (to build):

```
python -m radar.score --snap SNAP-003 --weights config/weights.toml [--override S1=0 ...] [--tag s1_zeroed] [--mode pipeline|scorer|both]
```

- **`--override`** applies weight edits in memory. The overridden config is serialised and hashed as its own `weights_sha256`, with a new `run_id` (`<base_run>+<tag>`). `runs.override_json` records the edits and `runs.base_run_id` the parent. **A run never overwrites another run's rows.** `run_id` is part of every signals/scores primary key.
- **`--mode both`** (default) writes, per wallet and scope:
  - `total_scorer`: all profiled wallets, controls included
  - `total_pipeline`: NULL for wallets with `passed_prefilter = FALSE`
  - Each control gets both columns plus its `passed_prefilter` flag.
- **Required for G2:**
  - (a) the main run
  - (b) the S1-weight-zeroed rerun (`--override S1=0 --tag s1_zeroed`)
  - (c) per-control scorer-only vs pipeline flags, from `--mode both`
- **Test:** run the base, then the override. Assert that the base run's rows are byte-identical before and after, and that the two `weights_sha256` differ. Break: make the override write into the base run_id → red.

## Δ5. Schema delta (on r4 §1)

```sql
funding_edges.direction TEXT CHECK (direction IN ('in','out'))
runs.base_run_id TEXT, runs.override_json TEXT, runs.mode TEXT
scores: total -> total_scorer DOUBLE, total_pipeline DOUBLE    -- pipeline NULL when passed_prefilter = FALSE
params: + s3_min_stake = 1000, s4_min_stake = 1000
```

## Δ6. Tests added to r4 §6

| test | break |
|---|---|
| S3 floor planted cases (Δ1) | set `s3_min_stake = 0` → 10 USDC ticket scores > 0 → red |
| S4 precision NA | drop the precision check → day-precision row computes → red |
| S6 in + out planted cases | remove the `out` edge type → the destination pair scores 0 → red |
| override isolation (Δ4) | write the override into the base run_id → red |
| weights search logs every config before selection (Δ3) | select before logging → log count < grid size → red |

## Δ7. Work that may start now (QA)

- Build of the §6 / Δ6 **tests and fixtures**.
- Measurement **D-b** (auto-redeem separability).
- Measurement **D-c** (|passed| and call budget, now including `out` log searches).

No signal code until r5 is cleared.
