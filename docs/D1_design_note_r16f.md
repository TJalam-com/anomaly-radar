# D1 design note r16f: addendum to r16e (S4 loader as ruled; fold-in items N1–N4)

Base: r16e (5b8f2ab1…). This addendum SUPERSEDES r16e §2's "No code change until ruled" line: QA ruled on 2026-09-27 and the code is folded into r15build. Structural only.

## 1. S4 loader on the input of record (G2E r3a, sha256 60d2c173…)

**Header map (QA ruling (a)):** explicit, in radar/events.py `HEADER_MAP`:
- r3a `event_ts_tz` → canonical `event_tz`;
- r3a `hedged_report_precision` → canonical `hedged_report_ts_precision`.

Declared header set `KNOWN_HEADERS` = the canonical roles + the two r3a names + the rest of the r3a header (read, never used by S4). Fail-closed: the loader refuses when
- a header is outside KNOWN_HEADERS;
- two headers map to one role;
- a required role is missing;
- `event_ts_tz_inferred` or `hedged_report_tz_inferred` is missing as a column.

**Inferred zone (QA ruling (b)):** `*_tz_inferred` is read as a strict boolean (true/1/yes, false/0/no; anything else refuses).
- inferred = true counts as an UNKNOWN zone, so the UTC+14 floor applies, consistent with replay R4a.
- QA N2: an EMPTY inferred flag next to a present timestamp refuses (no silent false). An empty flag is allowed only when that row's timestamp is empty.

**Label-offset parsing (defect fix, found while measuring):** r3a writes stated zones as labels (`IRST(UTC+03:30)`, `IST(UTC+02:00)`). The old `_tz` stripped "UTC", then failed on "IRST(+03:30)" and fell back to UTC+14.
- Now a zone is KNOWN only from an explicit numeric offset (bare UTC/GMT, ±hh[:mm], or NAME(UTC±hh:mm)). Zone NAMES are never looked up.
- A name without an offset, an out-of-range offset (> 14 h or ≥ 60 min) or any unparseable string stays UNKNOWN (the UTC+14 floor).

**Effect on the real r3a (lead 24 h):**
- 171 markets: 35 computable, 8 event_ts_too_coarse, 128 no_event_ts.
- E-001..E-004 (35 markets, IRST stated, not inferred): all anchored at 2026-02-28T04:30Z, not flagged unknown.
  - Under the old parser they would be floored to 04:00Z, a 30 min later anchor (less conservative).
  - Test: test_events_r3a::test_real_r3a_stated_irst_anchors_are_0430z_and_known. Break: the old parser → RED.
- Ruling (b) changes 7 anchors (E-006 ×5, E-007, E-008 have inferred zones) and no NA status.
- S4 input of record: score.py pins sha 60d2c173… (G2E_EVENT_TIMES_SHA256). Any other file refuses; a test-only `--event-times-sha` override marks the run g5_eligible = false.

## 2. Fold-in items (QA, 2026-09-27)

- **N1:** score.py refuses a SNAP whose manifest has no finished_at. There is no t = 0 fallback: t = 0 made every to_block_ts ≥ t completeness check trivially true.
- **N2:** see §1 (empty inferred flag with a present timestamp → refuse).
- **N3:** this note.
- **N4, dry-run projection (tools/dryrun_lookup_report.py):**
  - projection = N_L × MEAN calls per lookup;
  - p90 is reported as the upper bound, p50 is reported too;
  - hours = calls / the serial call rate measured on the single RPC lease (one consumer at a time).
  - Quantiles use a nearest-rank CEIL index, so p90 is conservative on small samples.
  - N_L for the projection = 19,876 (the PROF-001-scale ruled lookup set, r16b §2); the dry run's own N_L is reported but not used for the projection.
- **Harness evidence rule:** tools/break_g4.py runs every mutant BREAK_REPEAT times (default 3).
  - RED only if caught in every run.
  - Mixed outcomes = FLIP = BAD (probabilistic detector).
  - Anchor count ≠ 1 = BAD.
