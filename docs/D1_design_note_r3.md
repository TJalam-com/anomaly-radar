# D1 design note r3: delta on r2

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26.

**Base:** `app/docs/D1_design_note_r2.md`, CLEARED at 12246 B `d69acd3d01636976eb135727d30184b78480b005f5b44c88f8efed2bb28d58eb`. Everything in r2 stands unless changed below. r2 is not edited.

**Sources:**
- QA rulings for r3 (2026-09-26 ~11:15Z)
- QA ruling O5, which accepted the D2 key deviations
- `results/G1_A21_P2_2026-09-26.md` (7994 B `eafee86d…c4e2`)
- Principal decision D-004, relayed by QA

## Δ1. Time reference (replaces r2 §1 rows "resolution tx + instant" and "trade-close / T_ref")

| term | definition | source |
|---|---|---|
| `resolution_ts` | block timestamp of the CTF `ConditionResolution(conditionId)` log | chain (tenderly RPC), method = `app/tools/a21_p2.py` |
| `T_ref` | `max(ts)` over fills of that condition with `ts < resolution_ts` | `trades` |
| Gamma `closedTime` | **cross-check only**, never an input | G1: equal to `resolution_ts` on 170/171, 136 s early on 1 |

`T_ref` uses the **taker walk**. The `all` walk has the same fill instants, so the max is identical. The taker walk is specified so that the definition names one row set.

**Open edge, needs a ruling:** `ts` has 1-second resolution, and a fill in the resolution block can carry `ts == resolution_ts`. Strict `<` excludes it, even though within the block it may precede the resolution tx. Proposal: keep strict `<` (conservative), and measure on SNAP-003 how many fills have `ts == resolution_ts`. If the count is non-trivial, ordering by `(block, log_index)` needs a chain source.

## Δ2. Signal input window (adds to r2 §3.7)

- Signals **S3, S4, S5, S7** use only fills with `ts < resolution_ts` for the condition.
- **Post-resolution fills stay in `trades`.** The ingest does not delete them. They are excluded at signal time by one shared view:

```sql
CREATE VIEW signal_fills AS
SELECT t.* FROM trades t JOIN markets m USING (condition_id)
WHERE m.resolution_ts IS NOT NULL AND t.ts < m.resolution_ts;
```

- A condition with `resolution_ts IS NULL` contributes **no** fills to these signals. Their rows get `na_reason='no_resolution_ts'`.
- S1, S2, S6 and S8 are unchanged. S8 deliberately looks at post-resolution behaviour through `activity` REDEEM rows, not `signal_fills`.
- **G4 break-test (required):** plant one post-resolution fill in the test fixture that would change each of S3/S4/S5/S7 if counted. Assert the signal values are unchanged. Then point the signals at `trades` instead of `signal_fills` and confirm the test fails.

## Δ3. Schema changes (on r2 §2)

```sql
markets(  -- r2 columns, plus:
        resolution_block BIGINT,        -- (already in r2 text; now populated by ingest step 4)
        t_ref TIMESTAMPTZ,              -- Δ1, derived after trades load
        closed_at_delta_s INT,          -- resolution_ts - Gamma closedTime, recorded as cross-check
        scope_set TEXT                  -- Δ5; NULL until M_ext lands, then CHECK IN ('primary','ext')
       )
```

- Keys are as QA accepted in O5: trades PK `(snapshot_id, walk, seq)`, snapshots PK `(snap, path)`, one DuckDB per SNAP. They were in r2 marked pending and are now ruled.
- `signals.na_reason` gains the values `'no_winner'` (50/50 market, S5/win-rate only; QA ruling on `0x6f01b773…228a`) and `'no_resolution_ts'`.

## Δ4. Snapshot roles

| snap | role |
|---|---|
| SNAP-001 | VOID (killed run, no manifest) |
| SNAP-002 | dev run, control market only (manifest `417b06cb…0918`) |
| **SNAP-003** | **canonical G3 snapshot**: 171 M include conditions (D3, in progress) |

## Δ5. Secondary market set: placeholder, not built

- Per D-004, the values are `scope_set` ∈ {'primary', 'ext'}. `scores.scope` 'all' = primary only; 'all_ext' = ext only. They are never mixed.
- Planned enforcement, to be written when M_ext lands:
  - Ingest gate G-SCOPESET: a condition listed in both files aborts.
  - G4 test: every score row's contributing conditions share one `scope_set`.
  - `runs.market_sets_json` records both M file hashes.
- **Nothing is built until M_ext lands** (QA).

## Δ6. Ingest step 4 (r2 §3.4, now specified)

After step 3, for each loaded condition:
1. Find `ConditionResolution` with the G1 method.
2. Set `resolution_tx`, `resolution_block`, `resolution_ts` and `closed_at_delta_s`.
3. Cross-check the payouts against Gamma `outcomePrices`. A mismatch fails check `U-PAYOUT` (recorded, not aborting; G1 showed 171/171 match).
4. Compute `t_ref`.

Not in D3, which is steps 1–3 as instructed. Step 4 runs as a separate pass over SNAP-003, so steps 1–3 are not re-fetched.

The pass must not alter SNAP-003's manifested files:
- It writes new files `SNAP-003/step4/raw/rpc/...` and `SNAP-003/step4/resolutions_chain.parquet`.
- It writes its own manifest, `SNAP-003/step4/manifest.json`, which records the SNAP-003 manifest hash it builds on.
- `markets.resolution_*` and `t_ref` are joined from that file at signal time. The SNAP-003 DuckDB and parquet are not rewritten.
