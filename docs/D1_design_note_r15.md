# D1 design note r15: delta on r14 — Tester 1 review R14 (d1a9b831…) and QA rulings

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27. **Authored post-H-001c.** Tester 1 reviews this as a diff. No cut-off, rate or target.

**Base:** r14 `3ae23ea0…e107`. r14 is not edited. Δ18 (closure list), Δ20 and Δ22 were confirmed sound and are unchanged except as extended below.

## Δ24 (🔴 fix to r14 Δ21). Hub-capped counterparties are outside C(X)

- **Rule.**
  - C(X) = counterparties of X with `first_ts < t`, **not in the scope's stop set, not hub-capped** (their own logs exceed `hop_n_hub_cap`, recorded as `hub`), and ≠ the wallet.
  - F(X) = followed (selected ∧ complete ∧ child edges present).
  - `breadth_truncated(X, t)` = NOT(|C(X)| ≤ breadth ∧ C(X) ⊆ F(X)).
  - Hub-capped counterparties of path nodes are counted in `hub_capped` (per scope, run.json) and never cause NA.
- **Selection is unchanged.** The writer still ranks hub-capped counterparties in the top-breadth selection, as PROF-001 did (profile.py L185 ranks before the fetch reveals the hub). A selected hub therefore occupies a follow slot without expanding. The C(X) exclusion only removes it from the omission test; it does not free the slot.
  - Consequence: a node whose top-3 includes a hub and whose 4th candidate is pre-cut has |C(X)| (hubs excluded) possibly ≤ 3 but C(X) ⊄ F(X) → flagged. That is correct, because the 4th candidate was really not expanded.
- **Test.**
  - Node with ≤ breadth candidates, one of them hub-capped, all others followed → no NA, `hub_capped` = 1.
  - Control: the same node with the hub replaced by an ordinary unfollowed pre-cut counterparty → NA `breadth_truncated`.
  - Break: keep hubs in C(X) → the first case red.

## Δ25 (precedence). Hub-capped and stop-set nodes are not F-4 path expansions

- F-4 (r13 Δ14) requires every **expansion on the wallet's path** to be ok ∧ complete ∧ accepted writer.
  - Hub-capped nodes and stop-set nodes are design exclusions, not expansions. Their `hub` status (or not being fetched at all) never triggers `transfers_unverified`.
  - Only nodes the design intends to expand count: the wallet at hop 1, and selected, non-hub, non-stop counterparties at hop ≥ 2.
- **Test:** a wallet whose only non-ok record is a hub-capped hop-2 node, with everything else complete → S6/S1 computed (no `transfers_unverified`), `hub_capped` = 1. Break: treat hub status as an incomplete expansion → red.

## Δ26 (QA ruling). Writer pin: QA-held, append-only; readers never compare with their own disk code

- **Flow.**
  1. The Developer computes the r15 `writer_id` (Δ27) for the PROF-002 writer build and sends it to QA.
  2. QA records it in **`ledger/WRITER_PINS.md`**: QA-written, append-only, one line per pin (`writer_id`, closure list sha, date, scope "PROF-002").
  3. **The writer refuses to start** unless its own computed `writer_id` is listed in WRITER_PINS.md, read by path. It records the WRITER_PINS.md sha it checked in the manifest.
  4. **Readers** (score.py profile loader, replay, Tester 1's g5_eval) accept a profile only if **every** `files[]` entry's `writer_id` is in WRITER_PINS.md or on the QA allow-list (Δ19).
- **Withdrawn from r14 Δ19:** "the pinned id is recorded in PROF-002's manifest and checked against the running code closure". A manifest-held pin is self-attesting, and comparing with the reader's disk code breaks readability after any later edit.
- **Tests.**
  - Writer with an unlisted `writer_id` → refuses to start (no fetch attempted).
  - Reader: a `files[]` entry with a listed id → accepted; an unlisted id → REFUSED; WRITER_PINS.md missing → REFUSED.
  - Break: reader compares with its own closure instead of the pin file → red when a closure file is touched after writing.

## Δ27 (closure nit). writer_id also covers the interpreter and the lockfile

- The r14 Δ18 list gains `("python", sys.version)` and `("uv.lock", sha256(app/uv.lock))`.
  - uv.lock pins duckdb, pyarrow and httpx: duckdb runs the `targets()` SQL, pyarrow writes derived files, and httpx does the fetch.
  - If a version is not in uv.lock, the installed distribution versions from `importlib.metadata` for duckdb, pyarrow and httpx are added as well.
- The canonical JSON of the full sorted list is hashed to `writer_id`. The list is recorded once in the manifest (`writer_closure`) and its sha in every file entry.

## Δ28 (manifest shape, aligned with g5_eval r8)

- `manifest.json.files[]` has **one entry per bundle AND per fetch file**: `{path, bytes, sha256, kind: "bundle"|"fetch", writer_id}`. `writer_id` is a key of every entry. There is no second list.
- Top level: `writer_closure` (list), `writer_pins_sha256` (the WRITER_PINS.md checked at start), `follow_rule` (Δ30), `params_file`, `snap` ids and hashes.

## Δ29 (Δ17 nit). The no-PROF-001 check reads every PROF-001 manifest

The Δ17 refusal set = the union of `files[].sha256` over **every `manifest*.json` under `data/profiles/PROF-001/`**:
- `manifest.json` `a6fc4eaa…` (frozen);
- `manifest_2026-09-26T1955Z.json` `c33effad…`;
- `manifest_2026-09-27T0520Z_prefreeze.json` `c33effad…`;
- plus any later copy found by glob.

The test plants a bundle sha that appears only in the prefreeze copy → REFUSED.

## Δ30 (carried nit). Follow tie-break rule, recorded

- **PROF-001 (measured behaviour):** `sorted(candidates, key=-amount)` is a stable sort over the order in which counterparties first appear in the concatenated `eth_getLogs` result. So ties (978 truncating nodes) were broken by that order, which depends on how the RPC returns and bisects the logs.
- **PROF-002 rule (explicit, RPC-order-independent):** key = (−amount_exact, first_block, first_log_index, counterparty address).
  - amount_exact = the exact sum of the raw integer transfer amounts, with no float.
  - It is recorded in the manifest as `follow_rule`.
- **Test:** two counterparties with equal amounts, delivered by the mocked RPC in reverse order → same F(X). Break: a stable sort on arrival order → red.

## Δ31 (S1 fallback status)

- **At current params the Δ22 fallback is INACTIVE.** `funding_lower_bound_block = 0` (params v2 L36), so `lower_bound_ts` = Polygon genesis and `first_trade_ts < lower_bound_ts` never holds. An incomplete hop-1 `in` expansion therefore always gives S1 NA `transfers_unverified`.
- Any change to `funding_lower_bound_block` is a params change and **needs a QA ruling**. With a raised bound, the fallback sets t0 = ftt while unsearched funding may predate it. Then hours ≤ true hours, and S1 is biased upward (`ramp_down` is decreasing).
- The synthetic-bound test (Δ22 triple) is kept so the code path stays verified.
- Tester 1's precision on "only times" is accepted: t_bet depends on the wallet's own bet stake (`s1_min_stake`). That is not a transfer input.

## Δ32 (breadth-truncation scale → dry-run report)

- Tester 1's inference: in live and primary scopes (t = the SNAP instant), about 90% of S6 negatives become NA `breadth_truncated` at breadth 3 (from r11: 93% of wallets have ≥ 1 node with more than 3 candidates, all-time).
- The PROF-002 dry run (25 targets) reports, **per scope**:
  - the `breadth_truncated` rate among profiled wallets;
  - the distribution of |C(X)| over path nodes (hubs and stop set excluded): counts at 0, 1, 2, 3, 4–5, 6–10, 11–20, >20;
  - `hub_capped`.
  - QA can then price a higher breadth: fetch cost grows roughly with breadth^hops.
- **Breadth stays 3** unless QA rules otherwise after the dry run.

## Test list after r15

r14 list, with Δ19 reader tests replaced by Δ26, plus: Δ24 hub-in-C(X) (+ control, break); Δ25 hub not an F-4 expansion (+ break); Δ26 writer refuses unlisted / reader pin checks (+ break); Δ27 closure includes python + uv.lock; Δ29 prefreeze-only sha refused; Δ30 tie-break order independence (+ break).
