# D1 design note r16: S6 delta (S6-V1), standalone

Status: PROPOSAL for Tester 1 + Planner review and a QA ruling. Post-H-001c: it changes a signal.
Evidence rule: structural only. No control list, control score, rank or membership was read or computed for this note.

## 1. Problem (measured)

S6 today (r4 + H-001b + params v2): two profiled wallets are linked when they share a counterparty within 3 hops, in or out. Excluded: the stop list, and counterparties shared by more than 20 profiled wallets. S6 = min(1, linked/5).

On PROF-001 (interim run 20260927T090021Z-a0303f1a+prof001_interim):

| metric | value |
|---|---|
| linked pairs | 58,943 |
| S6 > 0 | 1,308 of 2,292 computed |
| saturated (linked ≥ 5) | 1,221 (93.3 %) |
| linked degree of positives | p50 42, p90 261, max 554 |
| largest connected component | 1,304 wallets |

Reproduced three times, each by a different mechanism:
- the Developer's rebuild (measure_clusters.py);
- the exporter's parity check (export_links: linked == S6 raw for every wallet);
- Tester 1's own code on the raw bundles (s6v1_out.json 649664ff…).

Tester 1 (s6v1b_out.json e1465eb2…): duplicated subtrees explain the fan-out spikes (9/12/20) but not the saturation. Only 3,093 pairs (5.2 %) are linked solely via duplicated subtrees.

## 2. New measurement for this note

Inputs:
- r16_edges.py (fb886c5c…) rebuilds the edges from the raw bundles, WITH the parent node (`via`).
  - Parity with the scorer's derived/transfers.parquet: 0 rows raw-only, 0 derived-only.
  - edges.parquet b26d3280…, hubs.parquet 7f2f8917….
- r16_variants.py (82876488…) measures every candidate. Output out/r16_variants.json (b21c76e0…).
  - The baseline reproduces 58,943 pairs exactly.
  - H1in and H1io reproduce Tester 1's numbers exactly (27,766 / 713 / 703 and 47,511 / 1,034 / 1,014).

**Global activity ("global fan") of a counterparty X** = the distinct counterparties observed when PROF-001 expanded X, or +∞ if X carries a hub/error marker (> 5,000 logs or a fetch error).
- Existing data gives it only for the 1,866 nodes PROF-001 expanded or marked: p50 5, p90 63, 241 hub-marked.
- **Coverage: 0.6–4.5 % of shared-counterparty rows** have a known global activity in most variants. The exceptions are the amount variants (A100 13.9 %, A1000 36.5 %) and G200s (100 % by construction).
- So a global-fan rule cannot be evaluated on PROF-001 without a new fetch (§5).

## 3. Candidates measured

Columns:
- pos = positives among the 2,292 S6-computed wallets.
- sat = share of positives with linked ≥ 5.
- ≥20 / ≥50 = share with linked ≥ 20 / ≥ 50 (the saturation share under a graded component with K = 20 / 50).
- p50 / p90 = linked degree of positives.
- comp = largest connected component.
- unk = share of positives whose links ALL go through counterparties of unknown global activity.
- ">200" = share of positives whose links all go through counterparties with known global activity > 200. It is at most 4.8 % in every variant except A1000 (14.2 %), because coverage is so low. G200s drops unknown-activity counterparties and scores 0 by construction.

| variant | pairs | pos | sat | ≥20 | ≥50 | p50 | p90 | comp | unk | >200 |
|---|---|---|---|---|---|---|---|---|---|---|
| V0 baseline (hop ≤ 3, in+out, fan ≤ 20) | 58,943 | 1,308 | .933 | .709 | .459 | 42 | 261 | 1,304 | .311 | .018 |
| F10 fan ≤ 10 | 34,904 | 1,089 | .869 | .578 | .360 | 28 | 193 | 1,074 | .400 | .024 |
| F5 fan ≤ 5 | 19,425 | 999 | .731 | .424 | .250 | 14 | 119 | 975 | .543 | .021 |
| F2 fan = 2 (pair-private) | 5,963 | 821 | .509 | .227 | .082 | 5 | 42 | 776 | .780 | .010 |
| G200 activity ≤ 200, unknown kept | 57,118 | 1,284 | .936 | .707 | .457 | 41 | 257 | 1,280 | .421 | 0 |
| G200s activity ≤ 200, unknown dropped | 6,459 | 743 | .821 | .293 | .038 | 16 | 35 | 701 | 0 | 0 |
| H1io hop-1, in+out | 47,511 | 1,034 | .876 | .659 | .479 | 44 | 263 | 1,014 | .334 | .039 |
| H1in hop-1 inbound funder | 27,766 | 713 | .875 | .638 | .450 | 40 | 218 | 703 | .421 | .013 |
| H1in-A100 (≥ 100 USD funded) | 10,873 | 562 | .829 | .486 | .260 | 19 | 106 | 550 | .326 | .048 |
| H1in-A1000 (≥ 1,000 USD funded) | 3,170 | 395 | .762 | .246 | .063 | 11 | 38 | 375 | .268 | .142 |
| H1in-F5 | 11,945 | 612 | .719 | .435 | .270 | 14 | 122 | 599 | .634 | .013 |
| H1io-F5-G200 | 16,173 | 852 | .719 | .426 | .255 | 14 | 117 | 827 | .669 | 0 |
| H1in-A100-F5-G200 | 2,980 | 412 | .575 | .231 | .070 | 6 | 42 | 395 | .667 | 0 |
| H1io-F2-G200 | 5,386 | 705 | .519 | .240 | .092 | 5 | 44 | 682 | .828 | 0 |
| V0-F5-G200 (hop ≤ 3) | 19,241 | 978 | .742 | .429 | .253 | 14 | 120 | 954 | .629 | 0 |

"G200" rows apply the activity bound only where activity is known. In these rows G acts on ≤ 1.5 % of rows, so they approximate the fan/hop/amount part alone.

Findings:
1. **The trace rule is not the lever.** Hop-1 only (H1io) still saturates 88 % and keeps a 1,014 component. Hops 2–3 add links (58,943 vs 47,511 pairs) but not the structure.
2. **The within-universe fan cap is the main lever measurable today.**
   - fan ≤ 20 → 5: saturation .933 → .731, p50 42 → 14.
   - fan = 2: saturation .509, p50 5.
   - fan counted inside 2,414 wallets cannot see services. Most links go through counterparties whose global activity is unknown: 54 % of positives at fan ≤ 5, 78 % at fan = 2, all links unknown.
3. **Funding amount helps funder-only variants**: ≥ 1,000 USD → p50 11, ≥ 20 share .246. But it drops the outbound side (H-001b).
4. **A giant component survives every candidate** (375–1,304). Pair-private links still chain transitively.
   - Component size is therefore NOT a usable acceptance criterion for S6.
   - V4 cluster grouping stays deferred under any candidate; the ego view is unaffected.
5. **Saturation is partly a full_at = 5 artefact.** A graded component `min(1, ln(1+linked) / ln(1+K))` with K = 20 cuts the top-bucket share by 22–53 points in every variant (V0 .933 → .709, F5 .731 → .424, C1 .719 → .426, G200s .821 → .293).

## 4. Candidate definitions (for the ruling)

**C1: direct shared counterparty, private, in+out.** Closest to the design intent: r4 "shared funder" + H-001b outbound-edge disclosure.
- Linked when the two wallets share a hop-1 counterparty (direct funder OR direct destination).
- That counterparty must be: not stop-listed; shared by ≤ F profiled wallets (F = 5 measured); and of global activity ≤ G (new fetch, §5).
- Component graded, K = 20.
- PROF-001 without G: pos 852, sat .719, ≥20 .426, p50 14, p90 117, comp 827.

**C2: shared direct funder, material.**
- Hop-1 inbound only; ≥ A USD funded by the counterparty to each wallet (A = 100 measured); fan ≤ 5; plus G.
- PROF-001 without G: pos 412, sat .575, ≥20 .231, p50 6, p90 42, comp 395.
- Tightest, but drops the outbound side that H-001b disclosed as part of S6.

**C3: pair-private counterparty.**
- Hop-1, in+out, the counterparty is shared by exactly 2 profiled wallets; plus G.
- PROF-001 without G: pos 705, sat .519, ≥20 .240, p50 5, p90 44, comp 682.
- Simple, but F = 2 is universe-size dependent: a larger universe turns private pairs into triples.

**Recommendation: C1 with the graded component (K = 20), with G supplied by the PROF-002 activity lookup (§5).**
- It is the only candidate that keeps both edge directions from r4/H-001b.
- F = 5 halves the p50 degree.
- G is the only rule that addresses Tester 1's finding: services pass a within-universe fan cap.
- Choosing F, G, K and A is a threshold decision. The note gives measurements, not values; the Planner/QA set them.
- Without the lookup, C1 still routes 67 % of positives entirely through counterparties of unknown global activity. S6 would then remain "under review" in the UI.

## 5. PROF-002 fetch design: CHANGES if C1 (or any candidate with G) is chosen

1. **Add a per-counterparty activity lookup** for every distinct hop-1 shared counterparty (fan 2..F) after the hop-1 fetch.
   - PROF-001 sizes: 16,661 counterparties at fan ≤ 20; 14,202 at F = 5; 8,942 at F = 2.
   - Two options, for the Planner:
     - (a) a capped transfer-log count: one eth_getLogs page with a cap C, activity = distinct counterparties in that page, "≥ C" when capped;
     - (b) eth_getCode + eth_getTransactionCount (2 calls, cheap). This separates contracts from EOAs and gives outbound activity, but not inbound.
   - Either way: new fetch records, with the same F-4 completeness semantics (unknown activity → the counterparty cannot make a link; NA-safe).
2. **Hops 2–3 no longer feed S6** under C1/C2/C3. PROF-002 could drop hop ≥ 2 expansions, a large RPC saving. The r15 F-4 path rules would then reduce to hop-1 completeness.
   - If QA prefers to keep hops 2–3 for later use (ego-view context), the fetch stays as designed and S6 simply ignores them.
3. **If QA keeps S6 as is (no G)**, the PROF-002 fetch design is unaffected.

## 6. Scoring and build impact

- S6 raw changes meaning (linked under the new rule) and its component changes (graded). Any score, weight search or G5 input computed with the old S6 is superseded.
- The r15 build gets the chosen S6 before PROF-002 scoring, per the QA order.
  - signals.s6 + path_flags: hop-1 only, fan F, activity G, graded K.
  - Tests plus planted mutants for each rule (F, G, K, direction, amount if C2).
  - The export_links rule text follows automatically, with parity to S6 raw kept.
- The UI keeps "S6 linked-wallet relation under review" until the ruling is applied to a served run.
