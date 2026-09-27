# D1 design note r12: delta on r11 — Tester 1 finding R11-1 (reverse cross-check)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27. **Authored post-H-001c.** Needs QA sign-off + Tester re-review. No cut-off, rate or target.

**Base:** r11 `198a41c6…79e9` (R10-1/2/4 and Δ11 closed). r11 is not edited. This note replaces how `followed` is defined in r11 Δ8 and adds the reverse assertion.

## Measurement (DIRECT EVIDENCE, the 2,414 preserved PROF-001 bundles, manifest `c33effad…`, each sha-checked)

Tools: `scratch/developer/r11/reverse_measure.py` `428208329abd6c85…`, `reverse_consistency.py` `62f100bc8d992a3c…`.

For each node X expanded at hop h < 3, take F(X), the top-3 set rebuilt as in r11. For each f ∈ F(X), look for evidence at hop h+1 that it was fetched:

| evidence for f at hop h+1 | count |
|---|---|
| child edges present (`via == f`) | 23,881 |
| hub record, label `hub`, n = 0 | 2,180 |
| hub record, label `ok`, n = 5355 (`0xc417fd8e…`) | 1 |
| **no record at all** | **3,406** (378 distinct address×direction pairs; 1,731 wallets) |

- **Why "no record" exists.** `profile.py` writes nothing for a fetch that returns 0 logs, and `get_logs` accepts an RPC `result: []` as complete. So "fetched and empty" and "not fetched or silently incomplete" look the same in a bundle.
- **Proof that some were incomplete.** For 40 occurrences (14 distinct address×direction pairs), an **earlier** fetch in PROF-001 saw logs for the same address and direction, yet this bundle has no record.
  - Every such log is dated 2026-09-26, minutes before the earlier fetch: transfers near the chain head on fetch day.
  - Two mechanisms fit, and the bundles cannot tell them apart because fetches are not recorded:
    - a stale in-memory hop cache: hop ≥ 2 reuses a result cached before the new logs, while hop-1 fetches bypass the cache;
    - a silent empty RPC answer.
- **Bound today:** for no no-record node did any fetch ever see a log dated before 2026-09-21. The known incompleteness is post-cut for every Feb–Mar 2026 cell. The unknown part (logs that no fetch ever saw) cannot be bounded from the bundles.
- **The same gap exists at hop 1.** Profiled wallets whose own fetch has status `ok` and 0 edges: 147 (in) and 223 (out).
- **The `0xc417fd8e…` entry (label `ok`, n = 5355) is explained.** It is in bundle `0x00425c69…` (`fetched_at` 2026-09-26T14:48:48Z).
  - That bundle is one of **6 pilot bundles** fetched before the pinned full run started (14:54:08Z). An earlier `profile.py` wrote them: its hub branch kept the raw status and log count.
  - All 2,180 hub entries from the pinned run read `hub`, n = 0.
  - The node has 0 child edges, so it was not expanded, whatever its label says.

## Δ12 (R11-1). `followed` is keyed on actual expansion, asserted both ways

- **(b) Definition.** `followed(X→f) = TRUE` only if f is in the rebuilt top-breadth set **and** the bundle holds f's expansion at hop h+1, meaning child edges with `via == f` in that direction. Otherwise `followed = FALSE` with `not_followed_reason`:
  - `not_selected` — outside the top-breadth set;
  - `hub` — hub record, of any label: `0xc417fd8e…` gets `hub` from n = 5355 > `hop_n_hub_cap` and 0 child edges;
  - `error:<status>` — error record;
  - `no_expansion_record` — the 3,406 above.
- A selected but unexpanded f therefore lands in C(X) \ F(X) whenever its edge from X has `first_ts < T_cut`, and the cell flags the wallet `breadth_truncated`. No label is trusted.
- **(a) Finalize asserts both directions.** It fails on any violation.
  - Forward (r11): every address with child edges at hop h+1 is in some rebuilt top-breadth set at hop h (0 violations today).
  - Reverse (new): every row with `followed = TRUE` has child edges at hop h+1, and every rebuilt-selected row without child edges has a `not_followed_reason` other than `not_selected`.
  - Keying `followed` on child edges makes the reverse direction true by construction. The assertion guards against a later code change that sets `followed` from the selection alone.
- **Schema.** Derived `transfers` gets `via`, `followed BOOLEAN` and `not_followed_reason TEXT` (r11 had `via` and `followed`). Raw bundles are unchanged. This is not built until cleared.

### Optional re-verification (not built unless QA asks; RPC cost measured before the run)

- Re-fetch the 378 distinct no-record address×direction pairs, plus the 370 empty hop-1 fetches, once each (`eth_getLogs`, stop on first 429, after the sweep).
- Pairs that come back with 0 logs, or only with logs dated after every cell's T_cut, get `followed = TRUE` with reason `verified_no_precut_logs`, so they are not flagged. Pairs with earlier logs stay `FALSE` and are flagged, and their logs are recorded in a side-car.
- Without the re-verification, the 3,406 stay flagged whenever their parent edge is pre-cut. That is conservative.

### Forward fix to `profile.py` (future PROF runs; not applied to the live bypass run)

- Every fetch writes an explicit expansion record `{hop, address, direction, status, n_logs (including 0), fetched_at, cache_hit, cache_fetched_at}`. An empty fetch is then recorded, and a stale cache hit can be seen.
- The 6 pilot bundles were written by pre-pinned code. Proposal: re-fetch those 6 wallets under the pinned `profile.py`, 6 bundles, into a new PROF version.

## Tests (Developer unit tests + mirror breaks; Tester plants independently)

- **RL-7e:** a wallet whose hop-1 node selects counterparty f (first_ts < T_cut). f's fetch returns an error; a second plant has no record at all.
  - In the cell, the wallet is flagged `breadth_truncated`, and f has `followed = FALSE` with reason `error:unavailable` or `no_expansion_record`.
  - Control: f expanded with child edges → not flagged.
- **Break 1:** set `followed` from the selection alone (drop the child-edge condition) → RL-7e red, and the finalize reverse assertion fails.
- **Break 2:** remove the reverse assertion and plant `followed = TRUE` without child edges in the derived rows → the finalize test red.
- **Label test:** a hub record labelled `ok` with n > cap and 0 child edges → `followed = FALSE`, reason `hub`.
