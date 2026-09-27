# D1 design note r14: delta on r13 — Tester 1 review R13 (7104f50d…) and QA rulings F1–F9

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27. **Authored post-H-001c.** Needs Tester review before build. No cut-off, rate or target.

**Base:** r13 `608457ff…b9e8`. r13 is not edited.
- This note **replaces** r13 Δ15 (layout, refusal and writer id) and the completeness definition in r13 Δ13.
- It adds F6 and F7 and closes D-r13.
- r13 M1, M2, Δ14 (per the F-4 ruling) and Δ16 stand, except where changed below.

## Closed (F9)

- **D-r13 is CLOSED** per plan r17 §11c: PROF-002 is a **full re-trace**.
- **Allow-listing the PROF-001 main-run bundles is CLOSED (not built)**: the writer bytes are unrecoverable (QA-verified).
- PROF-001 is plumbing-only: frozen at manifest `a6fc4eaa…`, never a G5 or final weight-search input.

## Δ17 (F2). PROF-002 contains no PROF-001 file

- PROF-002 = only bundles and fetch files written by the pinned r14 writer. **The r13 Δ15 "reuses PROF-001 bundles by path and sha" is withdrawn.** Bundles also carry activity, redeems and stats, which would otherwise feed G5 from an unverified writer.
- Scoring and replay refuse a PROF-002 manifest that lists any sha found in PROF-001's frozen manifest (`a6fc4eaa…`) or in its preserved manifests (`c33effad…`).
- **Test:** a manifest containing one PROF-001 bundle sha → REFUSED. Control: a clean manifest → accepted. Break: skip the cross-check → red.

## Δ18 (F3). Writer id = the code closure + params bytes

- `writer_id` = sha256 of the canonical JSON of the sorted list `[(module_path, sha256)]` covering every `radar` module that `profile.py` imports at run time, transitively.
  - Measured today (`sys.modules` after `import radar.profile`): `radar/__init__.py`, `radar/profile.py`, `radar/config.py`, `radar/entities.py`, `radar/snapshots.py`, `radar/vendor/__init__.py`, `radar/vendor/pselamy_entity_data.py`.
  - **Plus** `radar/scoring_view.py` and `radar/signals.py`. `profile.targets()` imports them lazily (`profile.py` L278), and they decide who is profiled, so a static import list alone would miss them. The r14 writer imports them eagerly at start.
  - Plus `("params", sha256(params file bytes))`.
  - The list is computed once all imports are done, before the first fetch, from `sys.modules` (every `radar.*` entry with a `__file__`), and **asserted unchanged** (re-hashed) before every file write.
- Every bundle and every fetch file records `writer_id` and the full list. The manifest records the list once and the `writer_id` per file.
- **Test:** editing `entities.py` (a stop-set change) changes `writer_id` while `profile.py`'s own sha is unchanged. Break: hash `profile.py` only → red.

## Δ19 (F1). Writer refusal: every file, pinned or allow-listed

- Scoring and replay refuse a profile **unless every bundle and every fetch file carries a `writer_id`, and each one equals the pinned id or is on `config/profile_writer_allowlist.txt`** (QA-cleared, sha-locked).
  - A **missing** `writer_id` (the PROF-001 case) → REFUSE.
  - A single unlisted id → REFUSE.
- The pinned id is recorded in PROF-002's manifest and checked against the running code closure when the profile is read.
- **Tests:**
  - all files without an id → REFUSED;
  - all files with one unlisted id → REFUSED;
  - one file with an id different from the rest → REFUSED;
  - all pinned → accepted.
  - Break: refuse only when there are more than one distinct ids (the r13 rule) → the first two red.

## Δ20 (F4/F5). Completeness from the record itself

A fetch record `R` for (address, direction) is **complete for an as-of instant t** iff:
1. `R.status == "ok"` and every sub-range has status `ok`;
2. `R.to_block` is an **integer resolved at fetch time**; the literal `latest` is never complete;
3. the sub-ranges, sorted by `from`, are **contiguous and gap-free**: `from₀ ≤ lower_bound_block`, `fromᵢ₊₁ = toᵢ + 1`, `last.to = R.to_block`;
4. `R.to_block_ts ≥ t`, where `to_block_ts` = the block timestamp recorded with `to_block`.

- A **cache hit** is complete iff the **cached record** satisfies 1–4 for t. `cache_fetched_at` plays no part. The r13 clause `cache_fetched_at ≥ t` is withdrawn.
- `followed = TRUE` requires: selected, `R` complete for t, and child edges present (r12 Δ12).
- **Tests** (each: complete control + planted defect → not complete):
  - planted gap (`fromᵢ₊₁ = toᵢ + 2`);
  - `to_block = "latest"`;
  - `to_block_ts < t`;
  - a cache hit with `cache_fetched_at ≥ t` but a cached `to_block_ts < t` → not complete.
  - Break: drop the contiguity check → the gap plant red.

## Δ21 (F6, QA ruling, all scopes). Hubs vs breadth truncation

- **Hub / stop set.** Counterparties excluded by the pre-registered stop set or by the hub cap (`hop_n_hub_cap`) are a design choice, not a missing expansion.
  - They cause **no NA**.
  - `run.json` reports `hub_capped` per scope: the count of wallets whose path has at least one hub-capped counterparty (the live analogue of replay's `hub_postcut`).
- **Breadth truncation.** When a counterparty that appears before the as-of instant is left unfollowed only because of the breadth cap, S6 "no link" becomes NA `breadth_truncated` in every scope (primary `all`, discovered, live, replay).
  - Rule for node X: C(X) = counterparties with `first_ts < t`, not in the scope's stop set, ≠ wallet; F(X) = followed. Flagged iff NOT(|C(X)| ≤ breadth ∧ C(X) ⊆ F(X)) (r11 Δ8), for a node on the wallet's path at hop < `s6_max_hops`.
  - Links actually found still count (S6 > 0 stays), with a flag count.
  - `run.json`: per scope, NA counts `breadth_truncated`, plus `s6_positive_on_breadth_truncated_path`.
- **PROF-002 dry run** (25 targets) reports the breadth-truncation rate: wallets flagged / profiled, per direction.
- Any change to `s6_hop_breadth` is a params change and needs a QA ruling. None is proposed here.
- **Tests:**
  - breadth + 1 pre-as-of candidates, one unfollowed → S6 NA `breadth_truncated`. Control: the extra candidate dated after t → computed.
  - hub-capped counterparty → no NA, counted in `hub_capped`.
  - Break: treat breadth truncation like a hub (no NA) → red.

## Δ22 (F7). What S1 depends on

- Code (`radar/signals.py` `s1`, read today):
  - S1 uses **only times**: `t0 = min(first_trade_ts, first_funding_ts)` (both optional), `t_bet` = the first qualifying bet, and `hours = max(0, t_bet − t0)` → `ramp_down`.
  - **No funder identity or amount** enters S1. `evidence_json` carries only `clamped` and `no_qualifying_bet`.
- Therefore, when the hop-1 `in` expansion is not complete (or its writer is not accepted), S1 does not use `first_funding_ts`:
  - if `first_trade_ts < lower_bound_ts` (a provably old wallet), then t0 = `first_trade_ts`, and S1 is computed. This is the existing funding-window rule and is sound, because t0 cannot be later than a first trade that predates the search window.
  - otherwise S1 = NA `transfers_unverified`.
- **Test:**
  - incomplete hop-1 + `ftt < lower bound` → S1 computed from ftt;
  - incomplete hop-1 + `ftt ≥ lower bound` → NA `transfers_unverified`;
  - complete hop-1 → computed from min(ftt, fft).
  - Break: use `first_funding_ts` from an incomplete expansion → red.
- If S1 ever gains funder identity or amount, this fallback must become NA `funding_window_truncated` (QA ruling F7); a guard test asserts S1's evidence keys.

## Δ23 (F8). M1 plant control

- Next to the M1 plant (drop a pre-fetch counterparty log → inconsistent occurrences +1), a **control**: drop a counterparty whose `first_ts` is after that occurrence's `fetched_at` → the counts are unchanged.
- Built in `scratch/developer/r13/partial_measure.py` (sha `4b2eede9…1eda0`) as `--plant` / `--control`. Both add a fake counterparty to the latest occurrence of node `0x0141be70…` (in), relative to the earliest occurrence's `fetched_at` 1790434487.
- Result (DIRECT):

| mode | fake first_ts | inconsistent occurrences | missing logs |
|---|---|---|---|
| base | none | 10,003 | 826,757 |
| plant | fetched_at − 1 s | **10,004** | **826,758** |
| control | fetched_at + 1 s | 10,003 | 826,757 |

## Build order (unchanged)

Sweep summary → code-review re-runs (harness on the current `break_g4` sha) → Tester review of r14 → build in the mirror → PROF-002 dry run (25 targets; RPC calls/wallet, projected hours, 429s, breadth-truncation rate) → QA → full run (one RPC consumer, stop on 429).

## Test list after r14

r13 list, with the r13 writer-refusal test replaced by the Δ19 set, plus: Δ17 PROF-001-sha refusal (+ control, break); Δ18 closure writer id (+ break); Δ20 gap / latest / to_block_ts / cache (+ break); Δ21 breadth NA / hub no-NA (+ break); Δ22 S1 fallback triple (+ break) and evidence-keys guard; Δ23 M1 control.
