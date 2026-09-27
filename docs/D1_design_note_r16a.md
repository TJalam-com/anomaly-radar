# D1 design note r16a: S6 delta, revision a (answers Tester 1 review R16-1..5)

Base: r16 (751893b1…). Review: scratch/tester1/code_review/R16_REVIEW_2026-09-27.md (4c36362d…).
Evidence rule unchanged: structural only. No control list, control score, rank or membership was read or computed.
The 385 bypass-only bundles (§5) were used only as extra fan-count population, in aggregate: no bypass address was printed, and no bypass wallet's links or S6 were computed or reported.

Instruments (scratch/developer/r16/):
- r16a_measure.py 3150ac88… → out/r16a_measure.json 4e625347…;
- edges_bypass.parquet 94d66b22…;
- params_v3_draft.toml 3499444c…6a6.

## 1. R16-1: §4 of r16 corrected (true no-G cores)

r16 §4 quoted the G200-where-known rows as "without G". The true no-G values, re-measured by my code, match Tester 1 (p90 ±1 is a quantile convention):

| core (PROF-001, no G) | pairs | pos | sat ≥ 5 | ≥ 20 | ≥ 50 | p50 | p90 | largest comp |
|---|---|---|---|---|---|---|---|---|
| C1: hop-1 in+out, fan ≤ 5 | 16,342 | 876 | .705 | .421 | .248 | 14 | 116 | 852 |
| C2: hop-1 in, ≥ 100 USD, fan ≤ 5 | 3,113 | 428 | .565 | .227 | .072 | 6 | 42 | 412 |
| C3: hop-1 in+out, fan = 2 | 5,410 | 714 | .513 | .237 | .091 | 5 | 44 | 687 |

These replace the r16 §4 numbers. The r16 §3 table itself is unchanged: its G200 rows are labelled as such.

## 2. R16-3: amount scale (A)

pUSD decimals were measured by eth_call `decimals()` on 2026-09-27: 6. USDC.e and USDC are 6 as well (scratch/developer/r13/token_decimals_2026-09-27.json c0e9d697…). profile.py's /1e6 is therefore correct for all three traced tokens, and A is correctly scaled. A matters only for C2, which is not recommended.

## 3. G: definition (option (a) only)

**activity(X, t)** = the number of distinct addresses Y ≠ X that have at least one Transfer log of a traced token (USDC.e, USDC, pUSD), from X to Y or from Y to X, at a block ≤ block(t).

**Lookup**, per hop-1 shared counterparty X that is not stop-listed:
- eth_getLogs over the 3 token contracts, with two topic streams: topic1 = X (outbound) and topic2 = X (inbound).
- Ascending block ranges from genesis (bound 0), with range bisection on provider limits, exactly as profile.fetch_logs does.
- Accumulate distinct Y. Stop when |Y| = C (C = G + 1), or at the head.

**Record**: {address, n_distinct (≤ C), capped, cap_block (the block of the log that made |Y| = C), first_block per Y (≤ C entries), to_block, to_block_ts, subranges, gap_free}.

**As-of T_cut (leak-safe for replay).**
- If capped and cap_block ≤ block(T_cut): activity > G.
- Otherwise activity(T_cut) = #{Y : first_block(Y) ≤ block(T_cut)}. This is exact, because every log below cap_block was read.

**F-4 semantics.** If the record is missing, not gap-free, unavailable, or has to_block_ts < t, the counterparty cannot make a link. It is never NA-inflating and never a guess.

**Option (b)** (getCode + nonce) is latest-state, which means hindsight in replay. It may serve only as an optional DROP pre-filter and never admits a counterparty. Default: off (params draft `activity_lookup_prefilter = "none"`).

**Cost.**
- Lookups: one per distinct hop-1 shared counterparty, 17,230 at PROF-001 scale with F off (§4).
- Calls per lookup: at least 2 streams × the bisected ranges until C is reached or the head. Services reach C quickly (few ranges); quiet addresses need the full history.
- Calls per lookup (p50/p90) will be measured in the PROF-002 dry run and reported before the full run. Hop-2/3 expansions are no longer fetched for S6 (s6_max_hops = 1), which offsets most of the cost.

## 4. R16-4: the value of G. Two options; I recommend (A)

**(A) Fixed structural G = 63 (C = 64), set now.**
1. Intent: S6 is "shared private funder or destination" (r4 + H-001b). A private counterparty transacts with few addresses; exchanges, relays and deposit services transact with thousands.
2. Structural evidence: log2 histogram of the known activity of the 1,642 PROF-001 nodes that were expanded (distinct counterparties seen when expanded):

   | bin | 1 | 2–3 | 4–7 | 8–15 | 16–31 | 32–63 | 64–127 | 128–255 | 256–511 | 512–1,023 | 1,024–2,047 | 2,048–4,095 | hub (> 5,000 logs) |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|---|
   | nodes | 268 | 425 | 267 | 235 | 158 | 127 | 52 | 50 | 25 | 16 | 10 | 9 | 241 |

   The largest bin-to-bin drop above the mode is 32–63 → 64–127: ×0.41, the only drop below 0.5. Above 64 the distribution thins into a separate hub mass.
   Caveat: this population is the top-3-by-amount counterparties, biased toward large ones. That makes the knee conservative for small shared funders.
3. G = 63 lies well above every within-universe fan used so far (5, 20). So a counterparty shared by more than 63 profiled wallets always has activity > G, and G subsumes the hub role of F.
4. A fixed value leaves no degree of freedom after PROF-002 data exists. It also allows the smallest cap (C = 64), which is the cheapest lookup.

**(B) Pre-registered blind formula, applied to the PROF-002 lookup results before any score is computed.**
- Take the distinct hop-1 shared counterparties with complete records.
- Build the histogram of activity over log2 bins [2^k, 2^(k+1)).
- Starting at the modal bin, move up until a bin's count is < 0.5 × the previous bin's count. G = 2^k − 1 for that bin's lower edge 2^k. Clamp G to [16, 256].
- (B) needs C ≥ 257 to see the histogram, so its lookups are costlier.
- On PROF-001's expanded nodes, (B) gives G = 63, the same as (A).

## 5. R16-5: fan universe, and whether F is still needed

Required, and adopted: fan (if used at all) is counted over the NATURAL universe only, i.e. prefilter-derived targets, with bypass-only wallets excluded (`s6_fan_universe = "natural"`).

Measured on PROF-001: links among the 2,414 natural wallets, with fan counted over natural wallets only vs natural + the 385 bypass-only bundles.

| rule | pairs (natural fan) | pairs (+ bypass in fan) | pairs lost | natural wallets losing links | natural positives zeroed | sat ≥ 5 (natural → + bypass) |
|---|---|---|---|---|---|---|
| V0 hop ≤ 3, fan ≤ 20 | 58,943 | 54,382 | 4,561 (7.7 %) | 685 | 41 | .933 → .928 |
| hop ≤ 3, fan ≤ 5 | 19,425 | 17,840 | 1,585 (8.2 %) | 500 | 15 | .731 → .714 |
| C1 core (hop-1, fan ≤ 5) | 16,342 | 15,138 | 1,204 (7.4 %) | 397 | 16 | .705 → .699 |
| C2 core | 3,113 | 2,636 | 477 (15.3 %) | 164 | 20 | .565 → .539 |
| C3 core (fan = 2) | 5,410 | 4,909 | 501 (9.3 %) | 365 | 25 | .513 → .501 |

No link is ever gained. Bypass wallets only push fan up. So a universe-counted F makes natural wallets' S6 depend on who else is in the universe: 7–15 % of links flip from the injected population alone.

**Is F still needed with G? No; recommend F OFF (`s6_fanout_max = 0`).**
1. F is universe-dependent, even over the natural universe: PROF-002's target count differs from PROF-001's.
2. F deletes exactly the strongest S6 pattern. A small private funder of 6 or more profiled wallets is removed by F = 5 and kept by G, because its activity stays small.
3. G subsumes F's hub role (§4 point 3).

If QA keeps F as a belt-and-braces rule, use F = 5 over the natural universe only.

The effect of C1 with G and F off cannot be measured on PROF-001, where activity is known for 1.5 % of hop-1 shared counterparties. It will first be measurable on the PROF-002 dry run, and the dry-run report will carry the same table (positives, saturation, graded shares, degree, component).

## 6. Proposed S6 (r16a) and params v3 draft

S6 (C1-G):
- Two natural or profiled wallets are linked when they share a hop-1 counterparty X, as inbound funder or outbound destination, with:
  - X not stop-listed;
  - activity(X, t) ≤ G with a complete record.
- linked(w) = the number of distinct such wallets.
- component = min(1, ln(1 + linked) / ln(1 + K)), K = 20.
- NA rules unchanged: transfers unavailable → NA; hop-1 record incomplete → transfers_unverified (r15 F-4); complete and empty → no_transfers_found.

Every threshold is in scratch/developer/r16/params_v3_draft.toml (3499444c…). It is NOT in app/config, NOT locked and NOT loadable.
- s6_rule "C1", s6_max_hops 1;
- s6_activity_max G = 63, s6_activity_cap C = 64, s6_activity_unknown "no_link";
- s6_fanout_max 0 (off), s6_fan_universe "natural";
- s6_component "graded_log", s6_graded_k K = 20;
- [fetch] activity_lookup "capped_transfer_log_count", activity_lookup_prefilter "none";
- A is not used by C1.

QA copies it to app/config and hash-locks it after the ruling, before any outcome-visible run.

R16-2 is QA's to confirm. From the Developer side (CLAIM): I have run no weight search and no look-half evaluation on any run with S6 computed. The only S6-bearing run is the PROF-001 interim run, which is g5_input = false.

## 7. PROF-002 fetch design (changes, restated)

1. Hop-1 transfers per target, both directions, as now.
2. NEW: an activity lookup per distinct hop-1 shared counterparty (§3), after all hop-1 fetches. That is 17,230 at PROF-001 scale; the count is known only after hop 1.
3. Hops 2–3: not fetched for S6. The r15 F-4 path rules reduce to hop-1 completeness plus lookup completeness.
4. One RPC consumer at a time, as before. The dry run (25 targets) reports calls per lookup and projected hours before the full run.
