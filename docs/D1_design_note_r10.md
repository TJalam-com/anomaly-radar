# D1 design note r10: delta on r9 §3 (replay) — Tester 1 findings R9-1..R9-7

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27. **Authored post-H-001c.** Everything here needs QA sign-off plus a Tester re-review. It uses only replay mechanics (R2/R3, R4/R4a, R8) and QA's rulings. It contains no cut-off, rate or target.

**Base:** r9 `cda454af…b52e` (§1–§2 accepted; §3 reviewed by Tester 1). r9 is not edited; this note changes §3 only.

## Δ1 (R9-1). Fetch-time caps and breadth choices are hindsight: record and flag, never silently use

**Facts (PROF-001):**
- The profiler made three all-time choices when it fetched, using the logs visible on 2026-09-26:
  - (a) hop-1 cap (> 50,000 logs → `capped`). It was hit by **0 of 2,414** wallets.
  - (b) hop ≥ 2 hub cap (> 5,000 logs → counterparty not followed).
  - (c) hop ≥ 2 breadth: the top-`s6_hop_breadth` counterparties by **all-time** amount.
- (b) and (c) decide which hop-2/3 edges exist in the data, so a replay cell could inherit post-cut knowledge through them.

**Design:**
- Replay does **not** recount logs; that would need a fresh per-(address, cut) `eth_getLogs`. Instead it records and flags.
- A new per-cell table `replay_s6_hindsight(condition_id, h, proxy_wallet, reason)` lists every profiled wallet in U(m,h) whose trace (in the enabled directions) touched any of:
  - `hub_postcut`: a counterparty not followed because of the hub cap, where that counterparty's first edge to the trace has `first_ts < T_cut`. Its pre-cut log count is unknown, so it might have been under the cap at T_cut.
  - `breadth_postcut`: a hop ≥ 2 edge that was followed only because of its all-time amount rank (breadth ranks are all-time, not as-of).
  - `capped`: a hop-1 cap (none today; kept for completeness).
- For such wallets, replay S6 is computed from the as-of edges that exist, and `evidence_json` carries `s6_hindsight: [reasons]`.
- Consumers decide how flagged cells enter any figure; the code only labels.
- Recount option, **not built unless QA asks:** for each hub address × distinct T_cut block, count its logs `< T_cut` with one `eth_getLogs`, and treat it as a hub only if that count is > `hop_n_hub_cap`. The cost needs measuring (hubs × distinct cuts).
- **Test RL-7c:** a planted funder whose logs are under the cap before T_cut but over it in the all-time data (marked hub in the bundle). In the cell, the wallet is listed in `replay_s6_hindsight` with `hub_postcut`, and S6 evidence carries the flag. Break: skip the hindsight scan → red.

## Δ2 (R9-2). Input allow-list extended to profile and transfer tables

The replay input audit (r9 §3.3) enforces column allow-lists per table. Reading any other column in a replay code path fails the run.

| table | allowed in replay (always with `ts < T_cut` filters where a time exists) | forbidden (all-time aggregates or post-cut facts) |
|---|---|---|
| `transfers` | `proxy_wallet, direction, hop, counterparty, token, first_ts_unix, tx_hash, log_index` (the first transfer's identifiers) | `amount` (all-time sum), `n_logs` (all-time count). Hindsight-only uses of either are routed through Δ1 flags, never through signal values |
| `wallet_profile` | `proxy_wallet, first_trade_unix` (used only if `< T_cut`) | `first_funding_unix` (all-time min over non-infra; replay recomputes it from `transfers.first_ts_unix < T_cut`), `lifetime_volume_usdc`, `activity_status / stats_status / transfers_status(_detail)` (derived from all-time data; replay derives its own per-cell status), `t_snap`, `funding_truncated`, `first_funding_block`, `lower_bound_ts_unix` |
| `redeems`, `trade_after` | none (S8 NA in replay) | all |
| `markets` | `condition_id, question, created_at` + token ids (r9 RL-8) | everything else |
| `trades` | `condition_id, token_id, proxy_wallet, side, size, price, ts` with `ts < T_cut` | `walk` is allowed only as the filter `walk = 'all'` |

- `first_transfer amount`: the profile stores the all-time amount per counterparty, not the first transfer's amount. It is therefore **not available** and not used; no replay signal needs it.
- **Test:** a replay code path reading `transfers.amount`, then `wallet_profile.lifetime_volume_usdc`, then `transfers.n_logs` → the audit flags each (3 planted reads, 3 flags). Break: disable the audit → red.

## Δ3 (R9-3). Replay `profiled` is as-of

- In cell (m,h): `passed_prefilter_asof = (Σ BUY stake at price < p_low over cell fills, all visible markets, ts < T_cut) ≥ pre_min_stake`. `profiled_asof = passed_prefilter_asof OR bypass_listed`.
- The live run's `passed_prefilter` / `profiled` flags are **never** read in replay.
- A wallet that passed live but not as-of is non-profiled in the cell: S1/S6 NA `not_profiled`.
- `universe.passed_prefilter` for replay scopes = the as-of value.
- **Data availability (proof):** cell fills ⊆ live pre-resolution fills (T_cut ≤ resolution_ts), and every stake term is ≥ 0. So `passed_prefilter_asof ⇒ passed_prefilter_live`. Every as-of-profiled wallet is therefore in PROF-001, either live-passed or bypass. The run asserts this and aborts on a violation.
- **Test:** a planted wallet with 600 USDC of low-odds buys before T_cut and 600 after. Live: passed. In the cell: `passed_prefilter = FALSE`, `profiled = FALSE`, S1 NA `not_profiled`. Break: read the live flag → red.

## Δ4 (R9-4). RL-3 split

- RL-3a (prefilter as of T_cut): the RL-3 plant (a wallet whose only large trade is after T_cut) → not prefiltered in the cell. This is Δ3's test.
- RL-3b: S2 is always NA in replay. The assert is `na_reason = 'replay_s2_unavailable'` for every (wallet, replay scope), and the same count as the universe. Break: compute S2 in replay → red.

## Δ5 (R9-5). Fan-out stop-list as of T_cut

- The high-fan-out rule counts distinct profiled-as-of wallets linked to a counterparty via edges with `first_ts < T_cut`, computed per cell.
- **Tests:**
  - (i) a counterparty reaching `s6_fanout_max + 1` linked wallets only after T_cut (the extra edges have `first_ts ≥ T_cut`) → not stop-listed in the cell, and it links the pre-cut wallets.
  - (ii) the same counterparty crossing before T_cut → stop-listed in the cell.
  - Break: count all-time edges → (i) red.

## Δ6 (R9-6). RL-9b plant (f)

- (f) E carries precision and tz, but the unit-start storage contract is unconfirmed → fallback anchor `a(E) = E − wid(p) − (1560 if tz inferred/unknown)`; cell `fallback_anchor = TRUE`, `event_coarse = TRUE`.
- The contract flag is an explicit per-row input (from G2E or QA's ruling), defaulting to "unconfirmed".
- Break: treat present precision as proof of contract → red.

## Δ7 (R9-7). Visible markets: RL-4 is "created < T_cut" (QA ruling)

- A market contributes to a cell iff `created_at < T_cut`. Its fills with `ts < T_cut` count, even if it resolved before T_cut (cross-market history is allowed).
- Its outcome is knowable only if `resolution_ts < T_cut`. No current replay signal uses outcomes: S5/S8 are NA, RL-5 is audited.
- The r9 wording "open at T_cut (created < T_cut < resolution_ts)" is withdrawn.
- **Test (RL-4, revised):**
  - (a) a market created at `T_cut + 1 s` with fills → absent from the cell.
  - (b) a market created before T_cut and resolved before T_cut, with pre-cut fills → its fills are present in the cell's prefilter sum.
  - Break: require `resolution_ts > T_cut` → (b) red.

## Test list after r10 (all Developer unit tests + mirror breaks; Tester plants independently)

RL-1, RL-2, RL-3a, RL-3b, RL-4 (a, b), RL-5, RL-6 (+ control), RL-7, RL-7c, RL-8, RL-9 (a, b), RL-9b (a–f), Δ2 audit (3 reads), Δ3 as-of profiled, Δ5 (i, ii).
