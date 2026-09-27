# D1 design note r9: params v2, P&L (G6 O2), replay implementation design

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27.

**Base:** r4…r8 (r8 `a1f63dae…60e8`, cleared). Those files are not edited.

**Authored post-H-001c.** On 2026-09-27 ~02:5xZ the Developer saw the judging sections of the replay pre-registration (QA error, recorded as H-001c in `ledger/QA_HELD_HASHES.md` `81c69ac1…`). Per QA's ruling, everything new in this note is flagged post-H-001c and needs QA sign-off plus a Tester review before it feeds any G5 figure.

The replay section (§3) uses **only** the mechanics QA allowed: R2/R3 (cells, scope, U(m,h), T_cut, strict `<`), R4/R4a (anchors) and R8 (RL-1..RL-9b). It contains no rank cut-off, no rate, no target and no judging rule, and none of them influenced any choice here.

## 1. Parameters v2 and provenance (record; already implemented and QA-cleared)

- **Params v2:** `config/params_frozen_2026-09-27_v2.toml`, 3540 B, `080cbbd0f9aaeccc6a75b625dbec9eb26e6df433f547e9382862c3fabafe4ac5` (+ `.lock`). v1 `467a62fb…836a` is kept as history.
  - `[params]` is identical to v1.
  - `[fetch]` changed (QA pre-registration ruling, before any S8 score existed): `user_signed_redeem_shapes = [safe_exec, direct_eoa, relay_hub, factory_direct]`; `relay_hub_requires_decode = true`; `factory_direct_rule`; `proxy_init_code_hash`.
  - Reason: relay_hub is 54% of first redeems (PROF-001). 40/40 decoded relay_hub redeems were owner-signed (signer == relayCall.from and CREATE2(Proxy Wallet Factory, keccak(from)) == wallet; 36 distinct wallets ↔ 36 distinct signers). 33/33 direct `factory.proxy` redeems came from the owner. Negative controls fired (nonce+1 → another signer; another owner → another address).
- **Disclosures (QA-required):**
  - "Owner-signed" proves the wallet's key signed the call, not that a human clicked. Embedded-wallet setups where a provider session signs for the user cannot be distinguished.
  - The proxy init-code hash `0xd21df8dc…a00b` is a recalled value from Polymarket client libraries, not read from verified source. It is accepted because it reproduces 40/40 wallet addresses exactly.
- **relay_hub enforcement:**
  - Without the S8 side-car (`<PROF>/s8check/shapes_v2.parquet`, built by `tools/s8_shapes.py` after the sweep), relay_hub → `relay_hub_unverified` → S8 NA `auto_redeem_unknown`.
  - With the side-car, relay_hub counts only where `decode_ok`.
  - NA stays for erc4337, MetaMask Delegation Manager, ERC-7579 execute, foreign Safe and other.
- **`signals.params_hash`** = sha256 of the WHOLE frozen params file bytes (= `run.json.params_file_sha256`). So v1- and v2-shape rows can never share a hash. The column did not exist before (it was specified in r4 and not built). Test and break are in place.
- **Run-dir contract v1.1:** `trades_summary_wallet.parquet` (scope, walk, proxy_wallet, fills_any_ts, fills_before_resolution, size_shares_any_ts) for EVERY wallet in U(scope) × both walks, zeros included. Contract string `run-dir v1.1 (design note r9)`.

## 2. P&L definition (G6 O2): display only, never a signal input

P&L is shown on the wallet page and in the cluster view. It is **not** an input to any signal or score: no weight, not in `signals`, and a grep guard is to be added to `tests/test_g4_guards.py` with a planted positive.

**Per (wallet w, condition c), non-void markets only.** Void markets (all payouts equal, e.g. `0x6f01b773…`) are excluded and listed. No P&L is shown for them.

| term | definition | source |
|---|---|---|
| cash_out | Σ `size·price` over w's SELL legs in c | `trades`, walk `all` (w's own legs; r4 §2 premise), **all timestamps** (post-resolution fills are real cash flows) |
| cash_in | Σ `size·price` over w's BUY legs in c | same |
| net_shares(t) | Σ BUY size − Σ SELL size of token t, all timestamps | same |
| payout | Σ_t net_shares(t) · payout_t, where payout_t = 1 USDC for the chain winner token and 0 otherwise (payout vector from the step-4 side-car) | `step4/resolutions_chain.parquet` |
| **pnl_trades(w,c)** | cash_out − cash_in + payout | — |

- **Units:** USDC-equivalent. Price is in collateral per share; pre-migration collateral is USDC.e (census). All M markets are pre-migration.
- **Fees assumption:** fees = 0.
  - Checks: Gamma `feesEnabled` per market (read from the SNAP-003 raw Gamma page, recorded per market); `positions.entry_fees_usdc` (0 in every sampled row so far).
  - If any M market shows fees enabled, or any positions row has `entry_fees_usdc > 0`, that market's P&L is labelled "fees not modelled".
- **Known limitations, shown with the figure:**
  - L-P1: split/merge/conversion (activity SPLIT/MERGE/CONVERSION) change positions without a trade. For profiled wallets where `v2/activity` shows such rows for c, the figure is flagged "position changes outside trades". For non-profiled wallets it is unknown and flagged "unchecked".
  - L-P2: a negative net_shares (sold more than bought via trades, i.e. shares from a split) makes payout negative for that token. It is shown, and flagged by L-P1.
  - L-P3: redemption is a transfer, not income. `pnl_trades` counts the payout at resolution whether or not the wallet redeemed.
- **Cross-check (labelled, never replaces the trades figure):** `positions_snap` `realized_pnl` / `total_pnl` for (w, token).
  - Coverage is partial: positions rows exist only with gross balance > 0.1 (F6), and they are post-redemption snapshots.
  - Reported per run: coverage %, the median absolute difference, and the count of |diff| > 1 USDC.
  - A disagreement is displayed next to the figure, not "fixed".
- **Aggregates:** per wallet over a scope = Σ over its non-void markets in that scope. Per cluster = Σ over member wallets. Both state the number of markets and the voids excluded.
- **Output (proposed contract v1.2, not built):** `pnl.parquet` (scope, proxy_wallet, condition_id, cash_in, cash_out, payout, pnl_trades, positions_total_pnl, flags). Scope rows mirror `universe` for the display scopes (condition, event, `all`).
- **Tests (to build):**
  - A planted wallet buys 100 @0.2 and sells 30 @0.5 on the winner token → pnl = 15 − 20 + 70 = 65.
  - A void market → excluded.
  - A post-resolution SELL counted.
  - A split flag when a planted activity SPLIT exists.
  - Break: exclude post-resolution fills → the planted case changes → red.

## 3. Replay implementation design (mechanics from R2/R3, R4/R4a, R8 only)

**Horizons** are a supplied list `h ∈ H_REPLAY` (a config input fixed by the replay pre-registration). The code does not hardcode values.

### 3.1 Cells, scope, cut

- **Cell** = (market m ∈ M include, horizon h). **Scope string** `replay:<h>:<condition_id>`, e.g. `replay:24h:0x3488…`.
- **T_cut(m, h) = min(anchor(m), resolution_ts(m)) − h.** `resolution_ts` comes from the step-4 side-car (chain ConditionResolution). A market lacking it is excluded and listed. The cross-check vs `results/G1_A21_P2` is already 171/171 equal.
- **Strict `<`:** an input counts only if its time `< T_cut`. `==` is excluded (RL-1).
- **U(m, h)** = wallets with ≥ 1 fill in m with `ts < T_cut(m,h)`. Every wallet in every cell is scored. No control list is an input; the bypass list affects only profiling, not the replay universe.

### 3.2 Anchor (R4a)

Candidates per m, from the canonical G2E table (path + sha256 recorded):
- E = `event_ts_utc` (+ precision, basis, tz flag)
- H = `hedged_report_ts_utc` (+ precision/tz when G2E provides them)

`a(v)`:
- **Contract met** (value stored as the start of its precision unit in the source zone, precision + tz flag present): `a(v)` = stored value.
- **Not met or unconfirmed:** `a(v)` = value − wid(precision) − (1560 min if tz inferred/unknown). A missing precision counts as day. The cell is marked `fallback_anchor`.
- wid: minute 1, hour 60, day 1440 (minutes).

Then:
- `anchor(m) = min(a(E), a(H))` over the candidates present; `anchor_basis ∈ {E, H}`.
- No E and no H → `resolution_anchored = TRUE`, and T_cut uses `resolution_ts` only.
- `u(m) = max(wid(p_win), spread_minutes) + (1560 if the winner's tz is inferred/unknown)`. A missing spread counts as wid.
- **event_coarse(m,h)** if `u(m) > h·60/3`, OR `fallback_anchor`, OR (`tier_gap` AND NOT (occurrence present with precision ∈ {minute, hour})).

The cell table records every flag. Which cells enter which figure is the pre-registration's business; the code only labels.

### 3.3 As-of-T_cut restriction of every input (RL-1..RL-8)

| input | restriction in cell (m,h) | implementation |
|---|---|---|
| fills (all markets) | `ts < T_cut(m,h)`; cross-market history allowed | per-cell view `cell_fills` over SNAP-003 `trades` (not `signal_fills`, since resolution plays no role in replay) |
| markets visible (RL-4) | `created_at < T_cut < resolution_ts` | market allow-list per cell; fills of other markets are dropped too |
| market metadata (RL-8) | allow-list only: `condition_id, question, created_at, token ids` | replay views expose only these columns. An input-column audit asserts that no other `markets` column is read |
| resolution-derived fields (RL-5) | forbidden: `resolved_outcome_index, payouts, void, chain_winner_index, t_ref, closedTime, gamma_volume` | the audit flags any read. **S5 and S8 = NA in every replay cell** (`na_reason='replay_no_resolution'`); both need outcomes |
| S4 event instant (RL-6) | only if `< T_cut`; by construction `T_cut ≤ anchor − h < anchor` | **S4 = NA in every cell** (`'event_after_cut'`). The rule is still evaluated per cell, so a planted anchor before T_cut would be used (tested) |
| prefilter (RL-3) | S3 raw over cell_fills (all visible markets, `< T_cut`) ≥ `pre_min_stake` | same SQL as live, over the cell view |
| funding edges (RL-2) | edge counted only if its `first_ts < T_cut` (the profile stores the first transfer per counterparty); hop-k edges likewise | filter on `transfers.first_ts_unix` |
| first_trade_ts / activity (RL-2) | earliest activity `< T_cut`, else none | the profile has the earliest activity ts: use it if `< T_cut`, else "no activity before cut" → S1 per the live rule with that t0 |
| first_funding_ts (RL-2) | earliest non-infra inbound `< T_cut` | from transfers with `first_ts < T_cut` |
| S2 lifetime volume (RL-3) | as of T_cut | **`user-stats` cannot be time-cut.** See decision R-D1 |
| entity labels (RL-7) | used only if `verified_at < T_cut` | `entities` gains `verified_at`. See decision R-D2 |

### 3.4 Decisions needed (post-H-001c, QA + Tester)

- **R-D1, S2 in replay.** Options:
  - (a) NA in replay (`'replay_s2_unavailable'`), which is cheapest and honest;
  - (b) `v2/activity?user=&type=TRADE&end=<T_cut>` sums for profiled wallets: full walks per (wallet, distinct T_cut) is heavy. Cost to be measured before choosing.
  - Proposal: (a).
- **R-D2, label knowledge time.** Every stop-list label was verified 2026-09-26, after every Feb–Mar cut, so a strict reading of RL-7 empties the stop-list in replay. Proposal:
  - Polymarket infrastructure labels get `verified_at` = the contract's deployment block time. A contract's role is knowable from deployment, and deployment precedes all M markets; the deployment tx is recorded per address.
  - pselamy-seed labels get `verified_at` = the seed commit time (2026-09-20), so they are excluded from replay.
  - The data-driven fan-out rule is recomputed as of T_cut (counts only edges with `first_ts < T_cut`).
- **R-D3, compute cost.** Cells = |M| × |H_REPLAY|, each a full signal pass over its cell view (~20 s measured live). Estimate ≈ 171 × |H| × 20 s. It runs offline, sequentially, one DuckDB per run, and is resumable per cell.

### 3.5 Code shape (to build after QA clears this section)

- `radar/replay.py`:
  - builds the cell table (anchors, flags, T_cut)
  - for each cell, creates views `cell_fills`, `cell_markets` (allow-listed columns), `cell_transfers`, `cell_profile`
  - runs `signals` with the replay switches (S4/S5/S8 forced NA with reasons; S2 per R-D1)
  - writes `universe/signals/scores` with the replay scope, plus `replay_cells.parquet`
- Run-dir contract applies, plus `replay_cells.parquet`: `condition_id, h, t_cut, anchor_basis, resolution_anchored, event_coarse, fallback_anchor, tier_gap_applied, u_minutes`.
- `run.json` records the G2E table path/sha, `H_REPLAY` and an `input_audit` block: every column read per table, compared with the allow-list.
- **The replay never reads the look file, the bypass list as a universe, control lists or pass criteria.**

### 3.6 Leakage tests (Developer unit tests; Tester plants independently in a scratch copy, per R8)

| # | planted | must hold |
|---|---|---|
| RL-1 | fill at `T_cut + 1 s` and at exactly `T_cut`, extreme size / low price | no signal or rank change vs without the plant |
| RL-2 | funding edge with first_ts `T_cut + 1 s`; earliest activity moved after T_cut | S1 and S6 unchanged |
| RL-3 | wallet whose only large trade is after T_cut | not prefiltered; S2 unchanged |
| RL-4 | market created at `T_cut + 1 s` with fills | absent from the cell; its fills not in any signal |
| RL-5 | a replay signal reading `resolved_outcome_index` / `payouts` / `t_ref` | the input audit flags it; S5/S8 NA in all cells |
| RL-6 | event instant at `T_cut + 1 s`; control: event instant at `T_cut − 1 s` | S4 NA in the first; the control is evaluated (proves the rule can admit) |
| RL-7 | entity with `verified_at = T_cut + 1 s` on a funder of a wallet in U | S6 unchanged vs no label |
| RL-8 | forbidden metadata column (closedTime / volume / tag) read | the allow-list audit flags it |
| RL-9 | (a) fill at `event_ts + 1 s` still `< resolution_ts − h`; (b) market with no G2E row | (a) the cut is event-anchored, so the fill changes nothing; (b) `resolution_anchored` flagged |
| RL-9b | (a) E minute + earlier H, fill at `a(H) + 1 s − h`; (b) E without precision, fill at `E − 1 day + 1 s − h`; (c) spread = 3·h·60; (d) tier_gap + occurrence day; (e) tier_gap + occurrence hour | (a) cut = a(H) − h, fill excluded, basis H; (b) fallback anchor, fill excluded, event_coarse; (c) event_coarse; (d) event_coarse; (e) not coarse from the tier_gap rule |

Each test gets a break mutation in the mirror harness: remove its filter → red.

## 4. Open

- `pnl.parquet` (contract v1.2) and the P&L guard test: build after this note is cleared.
- R-D1, R-D2, R-D3: QA / Tester.
- Replay code: not started; waits for clearance of §3 and the frozen G2E table.
