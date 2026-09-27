# D1 design note r4: G4 design (signals S1–S8, prefilter, weights format)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26. **Design only. No signal code until QA clears this file.**

**Bases, all cleared:**
- r2 `d69acd3d…58eb`
- r3 `5aecd575…a2bb`

r2 and r3 stay in force except where this note changes them.

**Evidence:**
- SNAP-003 manifest `9a901db6…3b83979` (canonical; G3 closed ~13:47Z)
- `results/G1_A21_P2_2026-09-26.md` `eafee86d…c4e2`
- `results/G3_zero_volume_chain_check_2026-09-26.md` `a49b6b91…5a81`
- `results/G3_F6_holders_vs_positions_2026-09-26.md` `cd76b8e7…caaa` (T2)

Blinding: the Developer has not read and will not read the control lists or pass criteria. Weight values come later, from `results/G2_split_look_2026-09-26.csv` (`72912c92…721d`) only.

## 0. Rules in force (collected: P1–P10 plus QA rulings since r3)

| # | rule | source |
|---|---|---|
| R1 | **Gamma volume is never a signal input and never the sole gate.** Market volume = Σ taker-walk `size` (shares) or Σ `size·price` (USDC), stated per figure | QA ruling on T2 M_ext facts (P7) |
| R2 | **Holders never used for stake/exposure** (holders = net per user, 6 dp, no floor). Stake from trades; current balance from positions (gross, 4 dp truncated, > 0.1) or chain | F6 closure (P9) |
| R3 | Signals S3/S4/S5/S7 read **`signal_fills`** only: fills with `ts < resolution_ts`. Strict `<` also drops same-second fills (2 on SNAP-003). Post-resolution fills (257 over 48 markets) stay in `trades` | r3 Δ1/Δ2 + QA ruling |
| R4 | `resolution_ts` = chain ConditionResolution block time. `T_ref` = max(ts) of taker-walk fills with `ts < resolution_ts`. Gamma `closedTime` = cross-check only | r3 Δ1 |
| R5 | 50/50 market `0x6f01b773…228a`: S5/win-rate VOID (`na_reason='no_winner'` for that condition's contribution); other signals compute normally | QA ruling |
| R6 | Volume/notional per market from the **taker** walk; per-wallet participation from the **all** walk. Every figure states its walk | QA ruling D2 |
| R7 | Bubble graph for resolved markets is rebuilt from trades at T_ref (positions/holders are post-redemption snapshots) | F6 closure |
| R8 | Scopes: condition, `event:<id>`, `'all'` (primary M, 171), `'all_ext'` (M_ext, own universe U_ext), later `'live'`, `'replay:<h>'`. Scopes are never mixed | D-004, D-006, P1 |
| R9 | Controls bypass the prefilter and are fully profiled. The prefilter rule and cut are recorded in `runs` and shown as a scope limit | QA ruling r2 §0.5 |
| R10 | NA ≠ 0: `signals.raw_value IS NULL ⇔ na_reason IS NOT NULL` (CHECK). NA contributes 0 to the total and is counted in `scores.n_signals_na` | G4 req 3 |
| R11 | D-007: `tests/test_env_on_d.py` must pass (interpreter, uv dirs, temp paths on D:) | P10 |
| R12 | G-TRADES trigger (r4 change): "0 taker rows while chain OrderFilled evidence > 0". Chain evidence = the D4 sampled method until Dune lands. U-UNITS and the §3.9 reconcile are **recorded cross-checks only** | P8 |

## 1. Schema delta on r3

```sql
ingest_results(condition_id TEXT PK, status TEXT, reason TEXT)   -- exists since D3 (P5)
markets.scope_set TEXT CHECK (scope_set IN ('primary','ext'))    -- populated when M_ext lands; SNAP-003 rows = 'primary'
-- runs: add
runs.params_json TEXT            -- every threshold of §3 as used; hashed with weights (§5)
runs.universe_json TEXT          -- per scope: |U|, |passed_prefilter|, |controls bypassed|, |profiled|
-- signals: add
signals.params_hash TEXT         -- sha256 of the [params] table actually applied
```

Manifest nit (P6): SNAP-003 `manifest.started_at` is null because the finalize was resumed. It is recorded here as a known gap and not back-filled. Fresh runs carry `run.started_at`.

## 2. Common definitions

Premise measured on 5 txs of `0x1a9c6729…` (SNAP-003, ~13:48Z): in the `all` walk, **each row is that wallet's own leg, with its own `side`, `token_id` and `price`**. A mint match pairs taker BUY Yes @p with maker BUY No @(1−p). Taker `size` = Σ maker `size`. Before build, a G4 test must re-assert this on ≥100 random txs across ≥5 markets; failure blocks the build.

| symbol | definition |
|---|---|
| `legs(w,c)` | rows of `signal_fills` with `walk='all'`, `proxy_wallet=w`, `condition_id=c`. These are w's own fills before resolution |
| `buys(w,c)` | `legs(w,c)` with `side='BUY'` |
| `stake(r)` | `r.size * r.price` in USDC-equivalent (price = USDC per share of the token bought) |
| `win_idx(c)` | outcome index whose chain payout = 1. **Void** if payouts are all equal ([1,1]) |
| `pos(w,c,t)` | net shares of token t at T_ref = Σ BUY size − Σ SELL size over `legs(w,c)` for token t. Trades only. SPLIT/MERGE/CONVERT via `activity` are **not** included (stated limitation, §7) |
| `bet(w,c,t)` | a (wallet, condition, token) with `pos > 0` at T_ref. `entry(w,c,t)` = stake-weighted mean BUY price of t over `legs(w,c)` |
| `U(scope)` | distinct `proxy_wallet` with ≥1 `all`-walk row (any ts) in the scope's conditions |
| `event_ts(c)` | instant the resolving event became public, from the Planner event table (not built yet). **Never** `resolution_ts` or `closedTime` as a stand-in (see S4) |

Clamp: `clip01(x) = min(1, max(0, x))`. Scale shape used by several signals:
- `logcap(x, cap) = clip01( log10(1+x) / log10(1+cap) )`
- `ramp_down(x, a, b) = 1` if x ≤ a, 0 if x ≥ b, linear in between

## 3. Signals: exact specs

All parameters are named `P.*` and live in the `[params]` table of the weights file (§5). The values shown are **proposals** and are frozen at the config hash before the first score run. Scope rule: for scope `'all'` / `'all_ext'` / `event:<id>`, "over c" means over every condition in the scope, unless a signal says otherwise.

### S1 fresh wallet
- **Input:**
  - `wallets.first_trade_ts` (earliest `v2/activity` row of any type, any market)
  - `wallets.first_funding_ts` (earliest inbound collateral transfer, `funding_edges`, tokens USDC.e/USDC/pUSD)
  - `buys(w,c)`
- **Formula:**
  - `t0(w) = min(first_trade_ts, first_funding_ts)`
  - `t_bet(w,scope)` = ts of the first BUY in scope with `stake ≥ P.s1_min_stake`
  - `raw = (t_bet − t0) / 3600` hours
- **Component:** `ramp_down(raw, P.s1_fresh_h, P.s1_stale_h)`. Proposals: min stake 1000 USDC, fresh 48 h, stale 720 h (30 d).
- **Units:** hours.
- **Window:** all history before `t_bet`.
- **NA:**
  - `'not_profiled'`: no activity walk
  - `'no_first_ts'`: activity and funding both empty
- **Not NA (component 0):** no qualifying BUY (the wallet made no large bet).
- **Never nonce** (census: proxy nonce = 1).
- **Planted test:**
  - fixture wallet with `first_trade_ts = t_bet − 2 h` and a 5,000 USDC BUY → component 1
  - same wallet with `first_trade_ts = t_bet − 60 d` → 0
  - the test also asserts NA when the activity rows are removed

### S2 concentration
- **Input:**
  - `activity` TRADE rows of w (lifetime, all markets; `usdc_size`, `side='BUY'`)
  - `buys(w,·)` in scope
- **Formula:** `raw = Σ stake(buys in scope, any ts before resolution) / Σ usdc_size(activity BUY, lifetime)`. The share of the wallet's lifetime buy volume that went into this scope.
- **Component:** `raw` (already in [0,1]; clip).
- **Units:** fraction.
- **NA:**
  - `'not_profiled'`
  - `'no_lifetime_volume'`: denominator 0
- **Theme scope:** for `'all'` this is the share in Iran-strike/leader markets (M), which is the brief's "one theme".
- **Planted test:**
  - wallet whose lifetime buys are 10,000 USDC, 9,500 of them in c → 0.95
  - add 90,000 USDC of unrelated activity → 0.095
- **Cross-check (reported, not a gate):** the numerator from trades vs `activity` rows for the same markets. A mismatch > 1% is flagged in `evidence_json`.

### S3 low-odds size
- **Input:** `buys(w,c)` (signal_fills, R3).
- **Formula:** `raw = Σ stake(r)` over `buys(w, scope)` with `r.price < P.p_low`. USDC committed at low odds.
- **Component:** `logcap(raw, P.s3_cap_usdc)`. Proposals: `p_low` 0.20, cap 100,000 USDC.
- **Units:** USDC-equivalent.
- **NA:** none. No low-odds buys → raw 0, component 0.
- **Both outcomes count.** Buying No at 0.10 is as low-odds as buying Yes at 0.10.
- **Planted test:**
  - one BUY of 50,000 shares @0.10 (5,000 USDC) → `logcap(5000,1e5)` ≈ 0.74
  - same shares @0.25 → 0
  - same BUY with `ts = resolution_ts` → 0 (R3 exclusion, per r3 Δ2 break-test)

### S4 pre-event timing
- **Input:** `buys(w,c)`, `win_idx(c)`, `event_ts(c)`.
- **Formula:**
  - `raw` = fraction of w's BUY stake in c that went to the **eventually-winning** token and was placed in the lead window `0 < event_ts(c) − ts ≤ P.s4_lead_h` hours
  - denominator = w's total BUY stake in c
  - scope aggregate = stake-weighted over conditions
- **Component:** `raw × logcap(stake_in_window, P.s4_cap_usdc)`. A large, well-timed bet scores high; a 5-USDC lucky tap does not. Proposals: lead 24 h, cap 50,000 USDC.
- **Units:** fraction × scale.
- **NA:**
  - `'no_event_ts'`: the Planner table has no instant for c
  - `'no_winner'`: void market
  - `'no_buys'`
- **Decision needed (QA/Planner):** until the event table exists, S4 is NA everywhere. I propose **no fallback to resolution_ts**. Resolution lags the event by hours to days, so a "lead" measured to resolution would reward ordinary late buyers.
- **Planted test:**
  - fixture `event_ts`; a 20,000 USDC winning BUY at `event_ts − 3 h` → component > 0.5
  - same BUY at `event_ts − 30 h` → 0
  - same BUY at `event_ts + 1 h` → 0 (after the event is not before it)
  - remove the `event_ts` row → NA `'no_event_ts'`

### S5 improbable win rate
- **Input:** `bet(w,c,t)` over the scope's **resolved, non-void** conditions; `entry`; `win_idx`.
- **Formula:**
  - For each bet i: `p_i = entry(w,c,t)`, the market-implied probability at entry, and `x_i = 1` if t is the winner.
  - `k = Σ x_i`, `n` = number of bets.
  - **Exact Poisson-binomial tail** `pval = P(X ≥ k)`, `X = Σ Bernoulli(p_i)`, computed by DP.
  - `raw = −log10(pval)`
- **Component:** `clip01(raw / P.s5_full_at)`. Proposal `s5_full_at = 6`, so p = 1e-6 gives 1.
- **Units:** −log10 p.
- **NA:**
  - `'n_resolved<min'`: n < `P.s5_min_bets` (proposal 3)
  - `'no_winner'`: every candidate bet is on void markets; void bets are otherwise just excluded
- **Not a raw win %.** Winning at 0.95 adds almost nothing ("98% on favourites is not a signal").
- **Per-scope:** per condition, n = number of tokens held (usually 1), so S5 is almost always NA there. It is meaningful at event / `'all'` scope.
- **Cluster-level pooling option** (P, from r2): pool the bets of one S6/S7 cluster into one test. It is a separate scope `cluster:<id>`, reported beside the wallet scope, never replacing it.
- **Planted test:**
  - 5 bets at entry 0.10, all won → pval = 1e-5 → component 0.83
  - flip to 5 bets at 0.95, all won → pval ≈ 0.77 → ≈ 0.02
  - 2 bets only → NA
  - one bet on the void market is excluded (the n count asserts it)

### S6 shared funder
- **Input:** `funding_edges` (≤ `P.s6_max_hops`, proposal 3) for profiled wallets; `entities` stop-list (cex / bridge / polymarket_infra / wrapper; plus high-fan-out funders above the threshold fixed at G2, QA-ruled).
- **Formula:**
  - `F(w)` = set of non-stop-listed funder addresses reached within `max_hops`
  - `raw` = number of **other** wallets in `U(scope) ∩ profiled` with `F(other) ∩ F(w) ≠ ∅`
- **Component:** `clip01(raw / P.s6_full_at)`. Proposal 5.
- **Units:** wallets.
- **NA:**
  - `'not_profiled'`
  - `'no_funding_found'`: no inbound collateral transfer located
- **Clusters:** connected components of the shared-funder graph are written to a `clusters` table (cluster view, bubble graph edges).
- **Planted test:**
  - 3 fixture wallets funded by one fresh EOA → each raw = 2 → 0.4
  - make the funder a listed CEX → 0
  - wallet with no funding rows → NA

### S7 coordinated timing
- **Input:** `buys` of every wallet in `U(scope)` (signal_fills).
- **Formula:**
  - A BUY is **qualifying** if `stake ≥ P.s7_min_stake` and `price < P.p_low`.
  - For each qualifying BUY of w on token t at ts, count distinct other wallets with a qualifying BUY of the same t within `|Δts| ≤ P.s7_window_s`.
  - `raw` = max of that count over w's qualifying buys in scope.
- **Component:** `clip01(raw / P.s7_full_at)`. Proposals: min stake 1,000 USDC, window 600 s, full_at 5.
- **Units:** wallets.
- **NA:** none. No qualifying buys → 0.
- **Rationale for the low-odds + size qualifier:** without it, every popular market at a news spike would score (the crowd buys together). Only the insider-shaped buys are compared.
- **Planted test:**
  - 4 wallets each buying 2,000 USDC of token t @0.12 within 5 min → each raw 3 → 0.6
  - spread them 20 min apart → 0
  - raise one price to 0.30 → that wallet drops out; the others' raw = 2

### S8 exit behaviour
- **Input:**
  - `activity` rows of w for c: REDEEM (and SELL after resolution)
  - `resolution_ts`
  - activity snapshot instant `t_snap`
- **Formula:** only for wallets with a winning `bet` in c.
  - `quick = 1` if the first REDEEM or SELL of the winning token happens within `P.s8_quick_h` hours after `resolution_ts`
  - `dormant = 1` if the wallet has **no** TRADE row in any market during `(redeem_ts, redeem_ts + P.s8_dormant_d days]`
  - `raw = 0.5·quick + 0.5·dormant`
- **Component:** `raw`. Proposals: quick 24 h, dormant 30 d.
- **Units:** score in {0, .5, 1}.
- **NA:**
  - `'not_profiled'`
  - `'no_winning_bet'`
  - `'dormancy_unobservable'`: `t_snap < redeem_ts + dormant_d`
  - `'auto_redeem_unknown'`: see blocker
- **BLOCKER before build (measure first):** Data API `v2/status` lists ingestion sources `auto_redeem_binary_redemption` / `auto_redeem_neg_risk_redemption` (census D9). The platform can redeem on the user's behalf. If an automatic redemption is indistinguishable from a user redemption in `activity`, `quick` measures the platform, not the wallet. A G4 measurement must show which field (if any) separates them. If none, S8 uses only `dormant`, `raw = dormant`, and the change is recorded.
- **Planted test:**
  - fixture winning wallet: REDEEM at `resolution_ts + 2 h`, no TRADE for 60 d, `t_snap` 90 d later → 1
  - add a TRADE 5 d after the redeem → 0.5
  - `t_snap` only 10 d after the redeem → NA `'dormancy_unobservable'`

## 4. Prefilter (per-wallet walks are too costly for all of U)

- **Rule (pre-registered here; values frozen at config hash):** `passed_prefilter(w) = TRUE` iff w has ≥1 BUY in `signal_fills` of any condition of the scope set with `price < P.p_low` **and** `stake ≥ P.pre_min_stake`. Proposal: same `p_low` 0.20, `pre_min_stake` 1,000 USDC. It is the S3 shape, so the prefilter never drops a wallet that could score on S3/S7.
- **Profiled** = `passed_prefilter OR is_control` (R9). Only profiled wallets get the `v2/activity` and funding walks. Hence S1, S2, S6 and S8 are NA `'not_profiled'` for the rest, and the NA is visible.
- **Recorded:**
  - `runs.prefilter_rule` (exact text above with values)
  - `runs.prefilter_cut_json` (|U|, |passed|, |controls|, |controls that would have failed|)
  - `runs.scope_limit_text` = "wallets without a ≥ {pre_min_stake} USDC buy below {p_low} were not profiled (S1/S2/S6/S8 = NA)"
- **Measure before freezing:** |passed| on SNAP-003 for the proposed values, reported with the call budget it implies (1 activity walk + funding lookup per profiled wallet). QA rules the values.
- **Planted test:**
  - a wallet with only a 900 USDC low-odds buy → not passed, S1/S2/S6/S8 NA `'not_profiled'`
  - mark it `is_control` → profiled, signals computed, `passed_prefilter` still FALSE

## 5. Weights file FORMAT (values later, LOOK half only)

`app/config/weights.toml`, one file, hashed into `runs.weights_sha256`. The run refuses to start if the hash differs from the one recorded when the weights were set.

```toml
version = "w-YYYYMMDD-n"
set_from = "results/G2_split_look_2026-09-26.csv"   # sha256 below; the held-out half is never an input
set_from_sha256 = "72912c92…721d"

[weights]            # non-negative; normalised to sum 1 at load (sum recorded)
S1 = 0.0
S2 = 0.0
S3 = 0.0
S4 = 0.0
S5 = 0.0
S6 = 0.0
S7 = 0.0
S8 = 0.0

[params]             # every threshold of §3/§4; hashed separately into signals.params_hash
p_low = 0.20
pre_min_stake = 1000
s1_min_stake = 1000
s1_fresh_h = 48
s1_stale_h = 720
s3_cap_usdc = 100000
s4_lead_h = 24
s4_cap_usdc = 50000
s5_full_at = 6.0
s5_min_bets = 3
s6_max_hops = 3
s6_full_at = 5
s7_min_stake = 1000
s7_window_s = 600
s7_full_at = 5
s8_quick_h = 24
s8_dormant_d = 30

[scopes]
compute = ["condition", "event", "all"]   # 'all_ext' added when M_ext lands; never pooled with 'all'
```

- **Score:** `total(w,scope) = Σ_i weight_i · component_i` over signals that are not NA. NA contributes 0 and increments `n_signals_na`. There is **no renormalisation** over the non-NA signals, so a wallet can never gain score from missing data (NA only weakens).
- **Ranking denominator:** stated per scope as `U(scope)` with the prefilter NA counts (R9).
- **Output labels (UI):** "anomalous pattern score". Never "insider" (brief §6).

## 6. Tests that must exist before any score run

| test | asserts | break that must turn it red |
|---|---|---|
| `test_all_walk_leg_semantics` | premise §2 on ≥100 txs / ≥5 markets | swap maker side in fixture |
| one planted-case test per S1–S8 | §3 "Planted test" lines | remove the planted row / move it across the threshold |
| `test_signal_fills_excludes_post_resolution` | r3 Δ2 | point S3/S4/S5/S7 at `trades` |
| `test_na_is_not_zero` | CHECK constraint + NA→`n_signals_na` | insert raw NULL without reason |
| `test_scope_all_row_per_wallet` | count(scope='all') = count(distinct scored wallets) | drop one wallet's 'all' row |
| `test_scopes_never_mixed` | every score row's conditions share one scope_set | add an 'ext' condition to an 'all' row |
| `test_weights_hash_enforced` | run refuses on a weights hash mismatch | edit one weight after hashing |
| `test_no_gamma_in_signals` | no signal query references `gamma_volume_shares` / Gamma fields (grep over signal SQL/code, with a planted reference to prove the grep fires) | plant a reference |
| `test_no_holders_for_stake` | no signal reads `holders_snap` | plant a read |
| `test_env_on_d` | exists (R11) | exists |

## 7. Open / decisions needed

| id | item | owner |
|---|---|---|
| D-a | S4 fallback: proposal **none** (NA until the Planner event table exists) | QA / Planner |
| D-b | S8 auto-redeem separability: measure before S8 build. If inseparable, S8 = dormant only | Developer measures, QA rules |
| D-c | prefilter values: measure |passed| and call budget on SNAP-003 before freezing | Developer measures, QA rules |
| D-d | all proposed `P.*` values: freeze before the first score. Changing any after the first score = new run + new hash, reported | QA |
| L-1 | `pos` ignores SPLIT/MERGE/CONVERT (activity only), so NegRisk conversions can mis-state net positions. S5/S8 bets on NegRisk markets carry an `evidence_json` flag until activity is joined | stated limitation |
| L-2 | chain completeness is SAMPLED (D4); full counts OPEN → Dune | recorded |
| L-3 | D4 mapping calibrated only for V1 non-negRisk pre-migration. NegRisk (81 of M) and V2/pUSD need their own calibration before G-TRADES' chain trigger applies to them | recorded |
