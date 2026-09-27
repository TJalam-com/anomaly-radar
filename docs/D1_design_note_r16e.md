# D1 design note r16e: fold-in notes (replay spec, S4 input of record, U16)

Base: r16d (c2e5b207…) and params v3 (73509e5f…).
Sources:
- Tester 1 fold-in review: R15V3_FOLDIN_REVIEW_2026-09-27.md (972bcf51…).
- QA G2E r3a clearance (2026-09-27).
- Nothing below changes a locked parameter.

## 1. Replay spec notes (carry into the replay build spec)

- **R15V3-1, per-cell path required.** signals.s6_v3 takes one block(t) / t per call and caches per wallet, so it is valid for live scopes only.
  - It now RAISES on any scope that is not 'all', a market condition or 'event:*'.
  - Replay needs a per-cell call with its own t = T_cut(m, h), block(T_cut) and a cache keyed by t.
- **R15V3-5, no_transfers_found is as-of.** It counts hop-1 rows with first_block ≤ block(t), never post-cut rows.
  - Already implemented in the prep for every t, so it also applies live at the SNAP instant.
  - Effect live: a wallet whose only hop-1 rows come after the SNAP block is NA no_transfers_found, not S6 = 0.
  - Tester 1 measured 46 of 2,292 such wallets at the PROF-001 median cut.
- **R15V3-2, no default t.** s6_v3 refuses a missing `_t_asof_unix`; the old default of 0 was fail-open.

## 2. S4 input of record (replaces the r7 reference to r3)

- S4 input of record: results/G2E_event_times_2026-09-26_r3a.csv, sha256 60d2c1733733bb5741f187723bd2f869b08261056fb94e03b91b41e481fb8229 (registry r3a 88c8362a…).
- score.py pins this sha (G2E_EVENT_TIMES_SHA256) and refuses any other event-times file.
- A test-only override (`--event-times-sha`) marks the run g5_eligible = false.

**FINDING: the r3a headers do not match events.COLUMNS.** Only the header line was read, after hash verification.

| r7 role | events.COLUMNS expects | r3a header |
|---|---|---|
| event_tz | event_tz | **event_ts_tz** (+ event_ts_tz_inferred) |
| report_precision | hedged_report_ts_precision | **hedged_report_precision** |
| report_tz | hedged_report_tz | hedged_report_tz (+ hedged_report_tz_inferred) |
| event_ts, event_precision, report_ts, tier_gap, occurrence_precision, condition_id | as named | present |

- As written, events.load_anchors REFUSES r3a (missing columns): fail-closed, so S4 would stay NA.
- Needed ruling:
  - (a) the header map (the two renames above);
  - (b) the semantics of `*_tz_inferred`. Under r7 an unknown timezone is floored at UTC+14. Is an inferred timezone "known"?
- Proposal (conservative): an inferred tz is treated as unknown, so the UTC+14 floor applies, unless QA rules otherwise.
- No code change until ruled. The event-times sha pin is already in the prep.

## 3. UI rule U16 (QA)

- The UI never renders G2E free text: notes, *_as_shown, source names, urls. Only the structured event time, precision and tier.
- The current exporter exports no G2E field at all, so it complies.
- When S4 event markers are added, the export must whitelist {condition_id, event time, precision, tier}. export_ui_data.guard gets a planted-violation test for any other G2E column.

## 4. Other fold-in items recorded here (tests + breaks in r15v3_prep)

- **R15V3-3:** the G record carries snap_manifest_sha256. g_record.check compares it with the profile manifest's and the scorer's SNAP.
- **R15V3-4:** run.json records the G ledger path + sha. Any ledger other than ledger/G_RECORDS.md → g5_eligible = false, with the reason listed.
- **R15V3-6:** the same record sha on two GREC lines with different fields → refuse.
- **R15V3-7:** per profile id only the last GREC line is valid; earlier records are refused as superseded.
- **Planner V1-1:** `--s6-edges in` sensitivity mode.
  - Same lookup set, lookup records and G.
  - Link edges and NA on inbound edges only.
  - run.json edge_mode.
- **Planner V1-2:** a lookup retry changes the lookup tables, so the old G record is refused (input binding). A new G record plus QA recording is required.
- **Planner V1-6:** under params v3 no scored signal reads hop ≥ 2.
  - S1 uses only the hop-1 inbound record and hop-1 funding edges.
  - S6 v3 reads hop = 1 only.
  - S2, S3, S4, S5, S7, S8 read no transfers.
  - Guards: profile.check_v3_fetch_params refuses s6_max_hops ≠ 1, and the scorer refuses a v3 profile with hop ≥ 2 rows.
- **Dry-run report (QA V1-3/V1-4), prep_tools/dryrun_lookup_report.py:**
  - N_L after hop 1;
  - calls per lookup p50/p90/max (all, capped, uncapped);
  - accepted span (largest ok leaf);
  - bisection trigger (result count vs block span vs other);
  - rate (calls/s, 429s);
  - projection to 19,876 lookups.
  - "Lease rate" is reported as calls/s plus the 429 count, pending QA's definition.
