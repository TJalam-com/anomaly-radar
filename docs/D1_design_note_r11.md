# D1 design note r11: delta on r10 — Tester 1 findings R10-1..R10-4

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-27. **Authored post-H-001c.** Needs QA sign-off + Tester re-review. Uses only replay mechanics and QA rulings. No cut-off, rate or target.

**Base:** r10 `07213e20…0ce2` (R9-2..R9-7 closed by Tester 1). r9 `cda454af…b52e` and r10 are not edited; this note changes r10 Δ1, Δ5, Δ7 and adds R10-3.

## Δ8 (R10-1). Breadth omission: flag `breadth_truncated`

**Measurement first (DIRECT EVIDENCE, PROF-001, the 2,414 bundles of preserved manifest `c33effad…`, each sha-checked):**
- The profiler stores, for every node it expanded, one edge per counterparty seen in that node's logs (`transfers.<dir>.edges[]` with `hop, via, counterparty, n, amount, first_ts`), including counterparties it did not follow (`profile.py` L183–184). Only the raw logs are not kept.
- So `n_counterparties_seen(node)` **is derivable**: count distinct `counterparty` per `(direction, hop, via)`. Candidates = those not in the fetch-time stop set and ≠ the wallet (the same filter as `profile.py` L185).
- Cross-check by a second mechanism: recompute each node's top-`s6_hop_breadth` follow set from its stored edges and compare with the addresses actually fetched at the next hop (edge `via` or hub/error `from_address`). Unexplained fetched addresses: **0**. Break (recompute with breadth 2): **5,626** unexplained → the check can fail. Tool `scratch/developer/r11/breadth_measure.py` sha `a25992897aed1085…`.
- `first_ts` is null on **0 of 2,932,413** edges.
- Counts (hops < `s6_max_hops`, where truncation decides what is fetched):

| | hop 1 (the wallet) | hop 2 |
|---|---|---|
| expanded nodes | 4,458 | 7,517 |
| candidates > breadth (3) | 2,443 | 5,643 |

  - 2,255 of 2,414 wallets have at least one such node (all-time).
  - 978 truncating nodes have an amount tie at the cut. The profiler breaks ties by log order (Python stable sort), so the tie is resolved but not by amount.
- Scope note: truncation happens when a node at hop h ∈ [1, `s6_max_hops` − 1] is expanded, and it decides which hop-(h+1) nodes are fetched. That includes the wallet itself at hop 1: the profiler applies breadth at every hop (`profile.py` L185–187). The module docstring "hops 2..max" means the fetched nodes. "hop≥2 expansion" in R10-1 is read as the expansion that produces hop ≥ 2 nodes.
- Side fact: one hub entry (`0xc417fd8e…`, hop 2, in) is recorded with `hub_or_error = 'ok'` and `n = 5355` (above the 5,000 cap). It was treated as a hub and not expanded. The label is inconsistent; the effect is correct. It is flagged `hub_postcut` per Δ1 like any hub.

**Design.** A traced node X at hop h is in the cell's as-of trace (reached through edges with `first_ts < T_cut`).
- Its **pre-cut candidates** C(X) = counterparties of X with `first_ts < T_cut`, not in the **cell's** as-of stop set (R-D2 labels + Δ5 fan-out) and ≠ the wallet.
- F(X) = the followed set: top-breadth by all-time amount under the **fetch-time** stop set, reconstructed as above.
- A counterparty that was stop-listed at fetch time but not in the cell (for example a pselamy-seed label, excluded from replay by R-D2) and has `first_ts < T_cut` lands in C(X) \ F(X). It is therefore flagged, which is correct: the as-of expansion would have followed it.
- The measurement above used the current `entities.rows()` as the fetch-time stop set. The 0-unexplained cross-check is consistent with that set being unchanged since the fetch. Finalize will record the stop-set sha it reconstructs with.
- The as-of follow set of X can only be drawn from C(X).
- If |C(X)| ≤ breadth, the as-of follow set is all of C(X), whatever the as-of amounts.
- `breadth_truncated(X, T_cut)` = NOT ( |C(X)| ≤ breadth AND C(X) ⊆ F(X) ).
  - Unflagged ⇒ every node the as-of expansion would fetch was fetched, so no omission is possible.
  - Flagged ⇒ omission is possible: either some pre-cut candidate was not fetched, or the as-of top-breadth among > breadth pre-cut candidates is unknown (as-of amounts are not stored; Δ2 forbids `amount`).
- This is a subset of the all-time rule "candidates > breadth" (flagged ⇒ |C| > breadth or some candidate not followed, and either implies > breadth candidates). The all-time rule also flags nodes whose extra candidates all appear after T_cut; those cannot hide a pre-cut omission.
- **Proposal: the T_cut-aware rule.** The all-time count `n_candidates_alltime` is recorded next to it, so the Tester's rule can be applied from the same table if QA prefers it.
- `replay_s6_hindsight` gets reason `breadth_truncated`, one row per (cell, wallet), with evidence `[{direction, hop, via, n_precut_candidates, n_candidates_alltime, n_followed}]`. It is added to S6 `evidence_json.s6_hindsight` like the Δ1 reasons.
- Only the expansion of nodes at hop < `s6_max_hops`, in enabled directions, is checked.
- It reads `transfers.proxy_wallet, direction, hop, counterparty, first_ts_unix` plus a new derived column `via` (the expanding node; it is in the raw bundle, not yet in derived `transfers`). It also reads a new derived column `followed BOOLEAN`, reconstructed at finalize from the stored edges with the fetch-time stop set and breadth, and cross-checked against next-hop fetches (finalize fails on any unexplained fetch). `amount` is used **only** inside that finalize reconstruction (all-time ranking = what the profiler did), never in replay signal values. The Δ2 allow-list gains `via, followed`.
- This is a **schema change** to derived `transfers` (+`via`, +`followed`). Derived files are rebuilt by finalize from the unchanged raw bundles, into a new manifest; raw bundle hashes stay the same. Not built until cleared.
- **Test RL-7d:** a wallet whose hop-1 node has breadth+1 candidates: breadth of them with `first_ts ≥ T_cut` and large amounts, plus one displaced candidate with `first_ts < T_cut` and a small amount (not followed). In the cell, the wallet is listed with `breadth_truncated`. Control: the same wallet with the displaced candidate's `first_ts ≥ T_cut` → not flagged (proves the rule can clear). Break: skip the C(X) ⊆ F(X) term → the plant is red.

## Δ9 (R10-2). Fills in a cell are bounded by each market's own resolution

This replaces r9 §3.3 row "fills" and r10 Δ7's fill clause (QA correction of the R9-7 ruling).
- `cell_fills(m,h)` = SNAP-003 `trades`, `walk = 'all'`, of visible markets m′ (`created_at < T_cut`, Δ7), with `ts < min(T_cut(m,h), resolution_ts(m′))`.
- `resolution_ts(m′)` = the chain resolution instant from the step-4 side-car. It is the same field and the same strict `<` as live `signal_fills`.
- Post-resolution fills of markets that resolved before the cut are excluded; their pre-resolution fills stay.
- RL-5 audit: `resolution_ts` is allowed **only** inside the `cell_fills` bound expression. Any other read (as a feature, or to derive an outcome) is flagged. Outcome fields stay forbidden.
- **Δ3 proof restored:** cell fills of m′ ⊆ {ts < resolution_ts(m′)} = live `signal_fills` rows of m′. Visible markets ⊆ M. Stake terms ≥ 0. So `passed_prefilter_asof ⇒ passed_prefilter_live`, and the run keeps its assert.
- **Test:** market m′ resolved before T_cut. Plant fill A at `resolution_ts(m′) − 1 s` (< T_cut) and fill B at `resolution_ts(m′) + 1 s` (< T_cut). In the cell, A is present in `cell_fills` and in the prefilter sum; B is absent. Break: drop the per-market resolution bound (use `ts < T_cut` only) → B present → red.
- r10 Δ7 test (b) still holds (pre-resolution pre-cut fills of an early-resolved market count). Its wording changes from "pre-cut fills" to "pre-resolution, pre-cut fills".

## Δ10 (R10-4). Break for fan-out test (ii)

- r10 Δ5 (ii) (counterparty crossing `s6_fanout_max` before T_cut → stop-listed in the cell) gets its own break. The mutation never adds fan-out counterparties to the cell stop-list (for example, the fan-out predicate is replaced by FALSE) → (ii) red.
- It is a separate mutation from Δ5 break (i) (count all-time edges), so each test has its own red.

## Δ11 (R10-3, QA ruled (a)). S6-weight-0 rerun per h

**Confirmed in code (DIRECT EVIDENCE, `score.py` L53–63 and L123–143):**
- `--override S6=0` is applied in `load_weights` before scoring, and yields a derived weights sha recorded in `run.json`.
- `score()` has no scope filter on weights: one applied weight vector multiplies every scope's components. So an override applies to replay scopes (`replay:<h>:<cond>`) exactly as to live scopes.
- One replay run with `--override S6=0` gives the S6-weight-0 scores for every h in `H_REPLAY` at once. Per-h figures are read by filtering scopes on h.

**Gap found (must be fixed in replay.py):**
- The W0 rule decides the computable signals from scope `'all'` only (L126–127). A replay run has no `'all'` scope, so every signal would count as non-computable and `score()` would raise "all applied weights are zero".
- **Proposal:** in replay, the computable set = signals with at least one non-NA component across the run's replay scopes. It is one set per run, like live, and it is recorded in `run.json`.
- Consequence to state: S4/S5/S8 are NA in every cell and S2 is NA per R-D1. So replay weights are the live weights renormalised over {S1, S3, S6, S7}; with the S6=0 override, over {S1, S3, S7}. The applied vector is in `run.json.weights_applied_json`.
- **Cost option (not built unless QA asks):** `score.rescore(run_dir, overrides)` re-scores from a stored `signals.parquet` without recomputing signals. The A4 recompute check already proves totals = stored weights × stored components. It writes a new run dir with `base_run_id` = the replay run. Without it, the S6=0 rerun repeats the full replay pass (≈ 171 × |H| × 20 s, R-D3).
- **Test:** a replay fixture run twice, base and `--override S6=0`. In every replay scope, total = Σ over non-S6 signals of applied weight × component. The S6 applied weight is 0 and the rest renormalise. The derived weights sha differs from the base. Break: apply the override only to scope `'all'` → replay totals unchanged → red.

## Test list after r11

RL-1, RL-2, RL-3a, RL-3b, RL-4 (a, b′), RL-5 (+ resolution_ts bound-only audit), RL-6 (+ control), RL-7, RL-7c, RL-7d (+ control), RL-8, RL-9 (a, b), RL-9b (a–f), Δ2 audit (3 reads), Δ3 as-of profiled, Δ5 (i, ii) with separate breaks, Δ9 fills (A present, B absent), Δ11 override on replay scopes.
