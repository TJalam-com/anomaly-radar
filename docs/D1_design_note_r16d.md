# D1 design note r16d: S6 delta, revision d (QA rulings R16C-1..2)

Base: r16c (5d5daaa7…) on r16b (88bba3bd…).
Review: scratch/tester1/code_review/R16C_QUICK_DIFF_2026-09-27.md (a6f78f74…): lock-ready once these two items are fixed.
Structural only.

Files (scratch/developer/r16/):
- params_v3_draft_r4.toml;
- knee_g.py 464df196…;
- test_knee_g.py 023d0990… (12 tests; 8/8 planted mutants RED).

## 1. R16C-1: the scope of t (replaces r16c §3, first bullet)

**t = the SNAP instant** for the G population (r16b §3, r16c §1) and for live scopes.

**Replay cells use t = T_cut(m, h)** for the link-edge cut (r16b §1.1: first transfer at a block ≤ block(T_cut)) and for the as-of activity (r16b §2), where block(T) = the last block with ts < T.
- G is the single recorded value in every cell.
- Read literally, r16c §3 would have admitted post-cut edges and post-cut activity in replay.

Test and break for the r15 fold-in:

| case | expected | planted break |
|---|---|---|
| replay cell, a link edge whose first transfer is after T_cut | excluded (no link in the cell; the link exists live) | cell uses the SNAP instant for the edge cut |
| replay cell, a counterparty whose Y set grows past G only after T_cut | as-of count at block(T_cut) ≤ G → qualifies in the cell | cell uses the SNAP activity |

## 2. R16C-2: capped at t, not capped at fetch

PROF-002 lookups run after the SNAP instant, so a lookup can reach C after block(SNAP). New rule:
- **capped_at_t** = capped ∧ cap_block ≤ block(t). Only this means activity > 256 (the terminal bin, or > G).
- A cap reached after block(t) says nothing about t: the activity at t is the exact count of first_block(Y) ≤ block(t).

knee_g.activity_at(record, block_t) is the single as-of rule. Both the G population (g_population(records, block_SNAP)) and the scorer use it; the r15 build imports it, and there is no second implementation.

Tests (test_knee_g.py):
- Tester 1's probe {complete, 2 natural sharers, capped, activity 10, cap_block after SNAP} → population [10], bin k = 3;
- cap_block == block(t) → capped at t;
- replay-style as-of: a record capped at block 500 has activity 3 at block 400 and "> 256" at block 500;
- a post-cut Y is excluded;
- a Y first seen IN block(t) counts, because block(t) is itself strictly before t.

Planted breaks, each RED with a named FAIL:
- "capped regardless of cap_block";
- first_block `≤` → `<`. This one first survived, so I added the block(t)-boundary test, and it now goes RED.
- The 6 earlier knee mutants stay RED.

## 3. Params v3 draft r4 (delta vs r3)

- s6_g_population: as-of activity at block(SNAP); only capped_at_t records go to the terminal bin.
- s6_t_scope (new): the SNAP instant for G and live scopes; replay uses T_cut(m, h) with the single G.
- s6_capped_at_t (new): the rule in §2.
- s6_g_reference_impl names activity_at.

Everything else is unchanged; non-S6 params are identical to v2.
