# D1 design note r13: delta on r12 — R12-1..R12-4 and F-4 (QA rulings, Tester 1 findings)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27. **Authored post-H-001c.** Needs Tester code review before any G5 use. No cut-off, rate or target.

**Base:** r12 `1af11431…dfae` (Δ12 accepted; R11-1 closed). r12 is not edited.

## Measurements (DIRECT EVIDENCE; PROF-001 preserved manifest `c33effad…`, 2,414 bundles, sha-checked)

**M1, partial expansions (R12-1).** Tool `scratch/developer/r13/partial_measure.py` `80b35997…caa56`.
- 6,244 address×direction nodes were expanded (had child edges). 349 of them were expanded in more than one occurrence (22,205 occurrences).
- An occurrence counts as inconsistent when another occurrence shows a counterparty whose first transfer predates this occurrence's `fetched_at`, but this occurrence lacks it.
- Result: **10,003 inconsistent occurrences** across **49** nodes, with 826,757 missing counterparty logs.
- **Every missing log is dated 2026-09-26 between 14:36:11Z and 18:04:11Z**, i.e. fetch-day activity, after every Feb–Mar 2026 cut. No missing log is pre-cut.
- The pattern fits the in-memory hop cache: hop ≥ 2 reuses the first result for the rest of the session.
- Can the check fail? Plant: drop one counterparty from one occurrence → inconsistent occurrences 10,003 → 10,004, missing logs +1.

**M2, writer identity (R12-3).**
- PROF-001 records **no `profile.py` sha**: none in the manifest meta (keys: bypass_list, calls_this_session, derived, fetch, files, finished_at, lower_bound_block_ts, params_file, snap, snap/step4 manifest shas, targets, workers), none in any bundle (keys: activity_asc/desc, calls, fetched_at, redeems, stats, t_snap, transfers, wallet), and none in `run_full.log` before the bypass resume (`profile.py=a3e8542424a3b7c6`, 02:48Z 2026-09-27).
- So the writer of the 2,414 main-run bundles cannot be verified from artifacts. Only the 6 pilot bundles are provably different: their `fetched_at` is before the run start, and they carry the old hub label.
- INFERENCE: the edit history in the session transcript could be used to reconstruct the writer sha per time window. That is not an artifact, and QA must rule on whether it may count.

**M3 (from r12).** 3,406 followed nodes have no record (378 address×direction pairs). 370 hop-1 fetches are `ok` with 0 edges. 14 address×direction pairs are known to be stale.

## Δ13 (R12-1). Completeness is required, not inferred

- **Definition.** An expansion is **complete for an as-of instant t** iff its fetch record shows:
  - status `ok`;
  - every sub-range of the bisection `ok`, covering `[funding_lower_bound_block, to_block]` where `to_block` is a **resolved block number** recorded at fetch time (never `latest`), with `block_ts(to_block) ≥ t`;
  - and either not a cache hit, or `cache_fetched_at ≥ t`.
- `followed = TRUE` requires `status ok ∧ complete ∧ child edges present` (r12 Δ12 plus completeness).
- **Required instant t.** Replay cell: `T_cut`. Live scopes: the SNAP's snapshot instant (the as-of of the trades).
  - Consequence: the M1 fetch-day inconsistencies (all dated after SNAP-003's instant) do not make an expansion incomplete for any scope in use today.
- **PROF-001 has no completeness markers** (no `to_block`, no sub-range record, no cache flag). So no PROF-001 expansion can be shown complete. The cross-occurrence check (M1) only detects inconsistency; it cannot prove completeness.
  - Until PROF-002 exists, each PROF-001 expansion is classified `partial_expansion` if M1 finds a missing log with `first_ts < t`, and `completeness_unrecorded` otherwise.
  - `completeness_unrecorded` counts as not complete (F-4 below).
- **Test RL-7f.** A truncated page: some child edges present, one pre-cut log dropped, and a second occurrence of the same node shows it.
  - Expected: that occurrence gets `followed = FALSE`, reason `partial_expansion`, and the wallet is flagged.
  - Control: the dropped log is dated after t → not partial.
  - Break: ignore completeness (`followed` = selected ∧ child edges) → red.

## Δ14 (R12-2 + F-4). NA `transfers_unverified`, in every scope and code path

- **Where.** Live scopes: `all`, `event:*`, `condition:*`, discovered. Replay cells. Both S1 (`first_funding_ts`) and S6.
- **Rule.**
  - Found edges are on-chain facts. A link or a funder found through any expansion counts as found, even on an incomplete path.
  - A **negative** needs verification. S6 "no linked profiled wallet" and S1 "earliest funder = X / no funder" are values only if **every** expansion on the wallet's traced path (all hops, enabled directions) is `ok ∧ complete (for t) ∧ written by a pinned or QA-allow-listed writer`.
  - Otherwise S6 and S1 `first_funding_ts` are NA `transfers_unverified`, never 0 or `no_transfers_found`.
- **S1 detail.**
  - `first_funding_ts` = the earliest non-infra inbound hop-1 edge. It is only as good as the completeness of the hop-1 `in` expansion.
  - If that expansion is not complete, S1 does not use `first_funding_ts`. If `first_trade_ts` alone decides t0 under the live rule, S1 is computed from it; otherwise S1 is NA `transfers_unverified`.
  - QA to confirm: this fallback reuses the existing funding-window rule, where `ftt < lower-bound` → t0 = ftt.
- **run.json.** Per scope: NA counts by reason, including `transfers_unverified`, plus `s6_positive_on_incomplete_path` (the count of S6 > 0 values that rest on at least one incomplete expansion).
- **Consequence today (INFERENCE from M2/M3).**
  - Under Δ13, no PROF-001 expansion is complete, so every S6 negative and every S1 `first_funding_ts` on PROF-001 would be NA `transfers_unverified`.
  - Only found links (S6 > 0) survive, flagged `s6_positive_on_incomplete_path`.
  - PROF-002 is therefore a precondition for S1/S6 carrying negatives at all. See decision D-r13 below.
- **Tests (R12-2 b).**
  - Plant: a hop-1 fetch that is `ok` with 0 edges and no fetch record → S1 and S6 NA `transfers_unverified`.
  - Control: a complete record with edges → computed.
  - Break: treat an empty unrecorded fetch as verified-empty → red.
  - F-4 plant: a found link on an incomplete path → S6 > 0 and counted in `s6_positive_on_incomplete_path`.
  - F-4 break: drop the path-completeness condition for negatives → red.

## Δ15 (R12-3 + R12-4). Writer identity and versioning

- **PROF-002 layout.**
  - The manifest reuses PROF-001 bundles by path and sha, byte-for-byte, never rewritten.
  - It adds new files: `fetch/<addr>_<dir>.json`, one per re-verification fetch, with `{address, direction, hop_context, status, n_logs (0 allowed), from_block, to_block, to_block_ts, subranges[{from, to, status, n}], fetched_at, cache_hit, cache_fetched_at, writer_sha256}`.
  - It adds the re-fetched pilot wallets as new bundles.
  - The manifest carries `writer_sha256` per bundle and per fetch file.
- **Refusal.** Scoring refuses a profile whose files carry more than one writer sha unless each sha is on `config/profile_writer_allowlist.txt` (QA-cleared, sha-locked). A file with no writer sha counts as "unknown writer".
- **The 6 pilot bundles:** S1/S6 NA `transfers_unverified` until re-fetched under the pinned writer into PROF-002.
- **The 2,414 main-run bundles:** writer unknown (M2). Under the refusal rule they carry "unknown writer". **Decision for QA:** either allow-list them as one class (with the M2 caveat), or re-fetch them.
- **Merging.** Re-fetches never merge into PROF-001-derived rows. PROF-002 derived rows are rebuilt from PROF-002's manifest (bundles by hash + fetch records). `run.json.profile` records PROF-002's manifest sha.
- **Forward fix to `profile.py`** (built after the bypass run ends):
  - every `get_logs` resolves `to_block` first and records sub-ranges;
  - the hop cache stores `cache_fetched_at` and `to_block`, and a hit is marked `cache_hit`;
  - a bundle records `writer_sha256` (sha of `profile.py` at process start, asserted unchanged at write).

## Decision D-r13 (QA): scope of the PROF-002 fetch

- (a) **Targeted:** only the r12 re-verification set (378 + 370 + 14) plus the 6 pilots. S1/S6 negatives stay NA for every wallet whose path includes any other unrecorded expansion, which is nearly all of them (Δ14).
- (b) **Full re-trace:** re-fetch every expansion of the 2,414 + bypass wallets under the pinned, recording writer. That is every hop-1 wallet×direction plus every followed node.
  - Cost is to be measured before any run: distinct fetches ≈ 2 × wallets + distinct followed nodes. PROF-001 had 6,244 expanded nodes plus 2,180 hubs.
  - Stop on the first 429, same discipline as the sweep.
- Proposal: (b). (a) leaves S1/S6 almost entirely NA, and so effectively removes them from G5.

## Δ16 (Tester nit, P&L display, score-neutral). Void market payout = NULL

- Today `pnl_market.payout` is 0.0 for a void market while `pnl_trades` is NULL, so "0 payout" can be misread as a real value.
- Change: `payout = NULL` when `void` (like `pnl_trades`). `pnl.parquet` already excludes void markets from its sums, so nothing else changes.
- Test: a void market → payout NULL and pnl_trades NULL; control: a resolved market → payout computed. Break: payout 0 for void → red.

## Test list after r13

r12 list + RL-7f (+ control, break) + Δ16 void-payout test (+ control, break) + Δ14 (R12-2 plant, control, break; F-4 plant, break) + writer-refusal test (mixed shas refused; allow-listed accepted; break: skip refusal → red).
