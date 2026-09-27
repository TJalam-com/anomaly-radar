# D1 design note r16b: S6 delta, revision b (applies the QA rulings on r16a)

Base: r16 (751893b1…), r16a (56e8e6bb…). Review: scratch/tester1/code_review/R16A_DIFF_REVIEW_2026-09-27.md (44f849e4…).
QA rulings: R16A-1..8, F stays OFF.

Evidence rule unchanged: structural only. No control list, control score, rank or membership was read or computed. The 385 bypass-only bundles were used only as population, in aggregate: no address was printed, and no bypass wallet's links or S6 were computed.

Files (scratch/developer/r16/):
- r16b_measure.py d07dde50… → out/r16b_measure.json d0342ad9…;
- edges_bypass_h1.parquet d1c5fc49…;
- knee_g.py 8c8a9ade… + test_knee_g.py b58f6b7f… (9 tests; 4/4 planted mutants RED);
- params_v3_draft_r2.toml 37cca510….

## 1. S6 definition (r16b, final text for the ruling)

Let t be the scoring instant, and block(t) the **last block with timestamp < t** (strict; R16A-7).
N is the NATURAL universe: the prefilter-derived targets. Bypass-injected wallets are not in N.

1. **Link edge.** Wallet w has a hop-1 edge to counterparty X when w received a traced-token transfer from X (inbound funder) or sent one to X (outbound destination), with the first such transfer at a block ≤ block(t), and X is not stop-listed.
2. **Lookup set** = the hop-1 counterparties (not stop-listed) shared by ≥ 2 profiled wallets (natural or bypass), of which ≥ 1 is natural (R16A-5).
   - Only these get an activity lookup.
   - A counterparty shared only by bypass wallets can never make a link, so it is not looked up.
3. **Activity** (two-direction measure, R16A-2): activity(X, t) = the number of distinct Y ≠ X with ≥ 1 traced-token Transfer X→Y or Y→X at a block ≤ block(t). It is read from the lookup record (§2).
4. **Qualifying counterparty**: X is in the lookup set, has a COMPLETE lookup record, and activity(X, t) ≤ G. G is derived blind (§3).
5. **Partners (natural-only, R16A-5)**: linked(w) = |{v ∈ N, v ≠ w : w and v both have a hop-1 edge to the same qualifying X}|. This holds for EVERY scored wallet w, natural or bypass.
   - Two bypass wallets are never partners of each other, whatever they share. Co-injection cannot create links.
6. **Component**: min(1, ln(1 + linked) / ln(1 + K)), K = 20.
7. **NA rules, in precedence order.** When several apply, the first one listed wins.
   1. transfers_unavailable: the hop-1 fetch is unavailable or capped (unchanged).
   2. transfers_unverified: w's own hop-1 record is incomplete (r15 F-4).
   3. **activity_unverified (R16A-3)**: w has ≥ 1 counterparty in the lookup set shared with a natural partner whose lookup is missing or incomplete, AND the verified component (links through complete lookups only) is < 1.0.
      - Verified links always count. If they already give component 1.0, the value is known regardless and S6 is not NA.
   4. no_transfers_found: complete and empty (unchanged).
   Otherwise S6 = the component (0 allowed: complete, verified, no qualifying partner).
8. **Counts in run.json, per scope**: n_lookup_set, n_lookup_complete, n_lookup_incomplete, n_capped, n_activity_unverified, n_s6_positive, and the G record hash (§3).

## 2. Activity lookup (fetch; R16A-6 merge order)

For each X in the lookup set:
- eth_getLogs over the 3 traced token contracts (USDC.e, USDC, pUSD; decimals 6 each, measured), with two topic streams: topic1 = X (outbound) and topic2 = X (inbound).
- **Per bisected block range [lo, hi], ascending from genesis:** fetch BOTH streams × all 3 tokens for that same range, merge by (block, log_index), and only then accumulate distinct Y. cap_block and first_block(Y) therefore come from one global order.
- Stop when |Y| = C = 257 (capped), or at the head.
- **Record**: {X, n_distinct (≤ 257), capped, cap_block, first_block per Y (≤ 257 entries), to_block, to_block_ts, subranges, gap_free}.
- **As of t:**
  - capped and cap_block ≤ block(t) → activity > 256 (> G for every allowed G);
  - otherwise activity(X, t) = #{Y : first_block(Y) ≤ block(t)}. This is exact, because every log below cap_block was read in global order.
- **Incomplete** (missing, not gap-free, unavailable, or to_block_ts < t) → X cannot qualify; see NA rule 3.
- **Pre-filter**: none. An optional getCode/nonce pre-filter may only drop, never admit (latest-state).
- **Cost projection (R16A-4).**
  - PROF-001 scale: the ruled lookup set has **19,876** counterparties (17,230 shared among natural wallets; +2,646 shared by a natural and a bypass wallet). 191 counterparties are shared only by bypass wallets and are not looked up.
  - Total calls ≈ 19,876 × calls per lookup. Calls per lookup (p50/p90/max, by capped/uncapped) are measured in the PROF-002 dry run, then projected to this count.
  - The dry run's own lookup count is NOT the projection: fan ≥ 2 grows with universe size.

## 3. G: derived blind by the pre-registered knee formula (R16A-2, option B)

**Population**: the COMPLETE lookup records of the full PROF-002 lookup set, with activity evaluated at t. The formula is never applied to the dry run.

**Bins**: k = floor(log2(max(1, activity))) for k = 0..7 (activity 0..255). Terminal bin k = 8 holds every activity ≥ 256, capped included.

**Formula (verbatim in params v3 `s6_g_formula`):**
- mode = the bin k in 0..7 with the largest count (ties: lowest k);
- from k = mode + 1 upward, the first bin with count(k) < 0.5 × count(k − 1) gives G = 2^k − 1;
- no such bin up to k = 8 → G = 256;
- clamp G to [16, 256];
- bins 0..7 all empty → G undefined: stop, report to QA, no fallback value.

**Procedure**, in order:
1. All lookups are complete, or accounted for as incomplete.
2. The G script (knee_g.py copied into radar/) runs over the lookup records BEFORE any scoring. It writes a G record: histogram, mode bin, G, input records hash.
3. The G record is reported to QA.
4. Tester 1 recomputes it independently (scratch/tester1/s6v1/knee_g.py).
5. QA records it (hash).
6. Only then does scoring run, reading G from the recorded file. The scorer refuses a missing or unrecorded G record.

**Reference implementation**: knee_g.py. Tests cover the PROF-001 proxy histogram (→ 63), mode ties, exact 0.5 (no drop), a drop into the terminal bin (→ 255), capped and ≥ 512 placed in the terminal bin, both clamps, and undefined G. Planted mutants, each RED with a named FAIL: tie → highest; `<` → `<=`; terminal rule removed; clamp removed.

**Disclosure**: the PROF-001 value G = 63 comes from a one-direction, top-3-by-amount proxy population. It is a proxy only and is NOT used. On two-direction counts the same cut would sit near ~45 on the proxy's scale (Tester 1: union/single-direction p50 1.40).

## 4. Corrections

- **R16A-1**: r16a §5 C2 row, with the same edge rule (hop-1 inbound, ≥ 100 USD) on both the natural and bypass fan populations: pairs 3,113 → 2,884, lost 229 (7.4 %), 108 natural wallets losing links, 11 zeroed, sat .565 → .552. (It matches Tester 1.) The loss range across rules is **7.4–9.3 %**, not 7.4–15.3 %. The conclusion (a universe-counted F depends on the universe) stands. F stays OFF.
- **R16A-8**: removed vs params v2:
  - `s6_full_at` (replaced by the graded component);
  - `s6_hop_breadth` (hops ≥ 2 no longer feed S6);
  - `s6_max_hops` 3 → 1;
  - `s6_fanout_max` 20 → 0 (F OFF; fan is diagnostic, over the natural universe).
  - [fetch] `hop_n_hub_cap` is kept for the record only.
  - v2's history header is carried verbatim into the v3 draft, under a new v3 header.
  - Every non-S6 parameter is identical to v2 (diffed).

## 5. Tests and breaks required in the r15 build (fold-in after the params v3 lock)

Each rule gets a test plus a planted mutant that must go RED with a named FAIL (strict: rc 1 + named FAIL):

| rule | test | planted break |
|---|---|---|
| natural-only partners (R16A-5) | a co-injected bypass pair sharing a private X → no link; a bypass wallet sharing X with a natural wallet → 1 link; natural↔natural → link | partners counted over all profiled wallets |
| lookup set | an X shared only by bypass wallets is not looked up and never links | set without the ≥ 1 natural condition |
| activity ≤ G | X with activity G links, G + 1 does not | `<=` → `<` |
| unknown → no link | an incomplete lookup never links | incomplete treated as activity 0 |
| activity_unverified (R16A-3) | w with verified component < 1 and one incomplete shared lookup → NA activity_unverified; with verified component 1.0 → value kept | NA rule removed; NA applied even at component 1.0 |
| merge order (R16A-6) | inbound Ys earlier than outbound, cap reached in the outbound stream → as-of count includes the inbound Ys; cap_block = the global one | per-stream accumulation (outbound to cap, then inbound) |
| block(T) strict (R16A-7) | a transfer in the block with ts == t is excluded | `<` → `<=` |
| graded component | linked 20 → 1.0, linked 0 → 0, monotone | linked / 5 restored |
| G record | the scorer refuses a missing/unrecorded G record; G read from the record, not params | the record check removed |
| knee formula | the knee_g tests above | the 4 mutants above |

## 6. PROF-002 fetch design (for the r15 build, before the dry run)

1. Hop-1 transfers per target, both directions (unchanged).
2. After all hop-1 fetches: build the lookup set (§1.2), then activity lookups (§2) with the global merge order.
3. Hops 2–3 are not fetched for S6.
4. G derivation (§3) before any scoring.
5. One RPC consumer at a time; stop on the first 429. The dry run (25 targets) reports calls per lookup, and the projection is 19,876 × calls per lookup (§2).
