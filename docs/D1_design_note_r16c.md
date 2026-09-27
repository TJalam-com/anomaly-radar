# D1 design note r16c: S6 delta, revision c (QA rulings R16B-1..3)

Base: r16b (88bba3bd…), which stands except where this note replaces text.
Review: scratch/tester1/code_review/R16B_DIFF_REVIEW_2026-09-27.md (862627d1…): CLEARED for the lock once these items are ruled.
Structural only; no control data.

Files (scratch/developer/r16/):
- params_v3_draft_r3.toml 4d9aa7bd…;
- knee_g.py 9fb0849b… (+ g_population);
- test_knee_g.py ada03b03… (10 tests; 6/6 planted mutants RED).

## 1. R16B-1: G population = natural-natural counterparties only

Replaces r16b §3 "Population":
- G is derived from the COMPLETE lookup records of counterparties shared by **≥ 2 NATURAL wallets** (17,230 at PROF-001 scale), with activity evaluated at the **SNAP instant**.
- The 2,646 counterparties that are in the lookup set only through a natural∧bypass share are still looked up, because bypass wallets need their natural partners. They never enter the knee histogram.

Reference: knee_g.g_population(records) keeps records with complete ∧ n_natural_sharers ≥ 2; capped → the terminal bin.

Test (test_bypass_induced_counterparties_do_not_change_g):
- adding 1,000 counterparties shared by < 2 natural wallets leaves G unchanged (63 in the test histogram);
- 999 incomplete records leave G unchanged.

Planted breaks, both RED with that test named:
- population over all sharers (≥ 0);
- incomplete records admitted.

## 2. R16B-2 (option i): S6 NA only when the verified component is < 1.0

Replaces r16b §1.7 rules 2 and 3. For S6 under params v3:
- **verified links** = partners found through w's hop-1 edges that exist in its record, to counterparties with a COMPLETE lookup and activity ≤ G.
- **verified component** = min(1, ln(1 + verified linked) / ln(1 + K)).

NA rules, in precedence order:
1. transfers_unavailable: unchanged.
2. **transfers_unverified**: w's own hop-1 record is incomplete AND the verified component < 1.0.
3. **activity_unverified**: w has ≥ 1 shared lookup-set counterparty (shared with a natural wallet) whose lookup is missing or incomplete, AND the verified component < 1.0.
4. no_transfers_found: unchanged.

If the verified component is 1.0, the value 1.0 stands even when rule 2's or rule 3's incompleteness applies. The wallet is then counted in **n_s6_positive_on_incomplete_path**, added to the r16b §1.8 run.json counts per scope, and the UI shows the S6-incomplete marker.

**This supersedes the F-4 wording "positive S6 on an incomplete path = flag, not NA", for S6 under v3 only.**
- Reason: with the graded component, a partial positive found on an incomplete path is only a lower bound. Its final value is unknowable unless the verified links already saturate it (1.0).
- Other signals' F-4 handling is unchanged.

Tests and planted breaks for the r15 fold-in (added to r16b §5):

| case | expected | planted break |
|---|---|---|
| own hop-1 record incomplete, verified linked = 3 (component < 1) | NA transfers_unverified | the F-4 literal (value + flag) |
| own hop-1 record incomplete, verified linked = 20 (component 1.0) | 1.0, counted in n_s6_positive_on_incomplete_path, marker | NA applied at 1.0 |
| one shared lookup incomplete, verified linked = 3 | NA activity_unverified | rule removed |
| one shared lookup incomplete, verified linked = 25 | 1.0, counted | counter not incremented |
| both rules apply, component < 1 | transfers_unverified (precedence) | precedence swapped |

## 3. R16B-3: instant and replay

- t = the **SNAP instant**, everywhere in r16b §1–§3.
- Replay uses the single recorded G. It is a global structural threshold derived from post-cut lookup data, as all params are, and there is no per-cell G. The replay report discloses this (`s6_g_replay = "single_global"`).

## 4. Params v3 draft r3 (delta vs r2)

- s6_g_population: natural-natural only, at the SNAP instant (R16B-1, R16B-3).
- s6_unverified_rule = "na_only_if_verified_component_lt_1" (R16B-2 (i)), with the supersession note.
- s6_g_replay = "single_global".
- s6_g_reference_impl names g_population + g_from_activities.

Everything else in r2 is unchanged, and every non-S6 parameter is still identical to v2.
