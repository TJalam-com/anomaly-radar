# D1 design note r7: S4 event-anchor spec (delta on r6)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26.

**Base:** `app/docs/D1_design_note_r6.md`, 14191 B `a149c6454cf64878323940589dde3440efc4c49d59cc8538d8233a59056f4350` (not edited).

**Source:** QA message 2026-09-26 on the r6 S4 spec. It replaces the S4 anchor, precision and NA rules of r4 §3 / r5 Δ1 / r6 Δ4 (A7). Everything else in S4 stands: the lead window, the winning side, floorlog, and the other NA reasons.

## Input

`results/G2E_event_times_2026-09-26_r3.csv` (Tester 2) plus its registry by `event_id`.

- It is read by path + sha256, recorded in `runs.event_times_path` / `runs.event_times_sha256` (A7).
- Column names are taken from the file as delivered. If a column named below is absent, S4 refuses to run (it does not guess).

## Anchor

For each condition c, via its event:

```
anchor(c) = min( start_of_unit(event_ts_utc,          event precision,  event tz),
                 start_of_unit(hedged_report_ts_utc,  report precision, report tz) )
```

- `start_of_unit(ts, precision, tz)` is the earliest instant consistent with the stated value. The timestamp is floored to the start of its precision unit (minute, hour, day, …) **in the source tz**, then converted to UTC.
- If the tz is unknown, use **UTC+14**, the earliest civil tz, so the floor lands as early as possible.
- A missing term is dropped from the min. If both terms are missing → NA `'no_event_ts'`.
- `anchor_precision_s` = the precision unit (in seconds) of whichever term gave the min. On a tie, use the finer unit.

Rationale (QA): an earliest-possible anchor can only move the window earlier. So a buy placed after the real event is never counted as "before" it, and S4 can only be weakened, never inflated.

## NA rules (added to r5 Δ1)

| na_reason | condition |
|---|---|
| `'event_ts_too_coarse'` | `anchor_precision_s > P.s4_lead_h × 3600` |
| `'event_ts_too_coarse'` | `tier_gap = true` **and** `occurrence_ts_precision ∉ {minute, hour}` |
| `'no_event_ts'` | no usable term for c |

## Window

The lead window is `0 < anchor(c) − ts ≤ P.s4_lead_h` hours, measured on `signal_fills` (still `ts < resolution_ts`, strict).

## Planted tests (replace the r5 S4 precision test)

1. Event 2026-02-28, precision **day**, tz unknown, lead 24 h:
   - anchor = 2026-02-28 00:00 UTC+14 = **2026-02-27 10:00Z**
   - precision 86400 s > 24 h → NA `'event_ts_too_coarse'`
   - Break: use UTC instead of UTC+14 → the anchor moves 14 h later → the assert on the anchor value goes red.
2. Event 2026-02-28 06:15 **minute**, tz UTC; hedged report 2026-02-28 05:00 **hour**, tz UTC:
   - anchor = 05:00Z, from the report term (the min), precision 3600 s → computed
   - a 20,000 USDC winning BUY at 04:30Z scores; one at 05:30Z scores 0
   - Break: take max instead of min → the 05:30Z buy scores → red.
3. `tier_gap = true` with `occurrence_ts_precision = day` → NA. The same row with `occurrence_ts_precision = hour` → computed. Break: drop the tier_gap rule → red.
4. The event file's sha256 differs from the one recorded for the run → the run refuses.
