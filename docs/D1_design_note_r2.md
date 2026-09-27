# D1 design note r2 — data model + ingest flow

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26.

Supersedes `app/docs/D1_design_note.md`. r1 was cleared at 14168 B `490a2a2d…2f6f` and is kept unchanged as history.

Evidence:
- `results/D1_endpoint_census_2026-09-26.md` (r1, 12779 B `108ded89…1133`)
- `results/D1_endpoint_census_2026-09-26_r2.md` (8810 B `d61433ea…928b`)
- D2 ingest SNAP-002 (manifest `417b06cb…0918`)

What changed from r1:
- **R1.** F5 retracted: units error plus double count (census r2 M1/M2).
- **R2.** Resolution instant comes from the chain log (census r2 M3/M4).
- **R3.** QA rulings of 2026-09-26 folded in (§0).
- **R4.** D2 key/storage deviations, marked **PENDING RULING**.
- **R5.** G4 output requirements added: scope='all', `passed_prefilter`, NA flags.
- **R6.** RPC choice.

## 0. Rulings in force (QA, 2026-09-26)

1. **Trades source**: `v2/trades` is primary, walked twice per condition.
   - `walk='taker'` (default `taker_only=true`) = each fill once. Source for **market volume / notional / fill counts**.
   - `walk='all'` (`taker_only=false`) = taker leg + maker leg(s). Source for **per-wallet participation, maker activity**.
   - Every figure states its walk. No `is_taker` column.
   - Reconcile gate §3.8 compares like units: Σ taker-walk `size` (shares) vs Gamma `volume` (shares); fill count vs Dune (when key lands).
2. **API trade rows are not deduplicated.** Surrogate key. `(tx_hash, log_index)` dedupe applies only to chain/Dune rows.
3. **RPC**: tenderly gateway default, quiknode public fallback (census r2 §E). Rate limit to be measured before bulk `eth_getLogs`. Dune when the key lands.
4. **Derived schema, rebuild not migrate, no Alembic.**
5. **Prefilter allowed.** (a) All positive and negative control wallets bypass it and get full activity + funding walks. (b) The rule and its cut are recorded in `runs` and shown as a scope limit.
6. **Vendoring**: pselamy `entity_data`/`entities` only, as a seed. Keep LICENSE + attribution, `verified_on_polygon=false`. The rest is ideas only.
7. **Identity**: derived tables and UI drop `name, pseudonym, bio, profile_image(_optimized), display_username_public, verified`. Raw snapshots stay byte-exact, local, gitignored.
8. **Market set** = canonical M `results/G0_M_phase1_markets_2026-09-26.csv` (413692 B `63e53bcf…e8ea`), `include==True` → 171 conditions. No Developer-picked lists.

## 1. Data needs → source

| need | source | evidence |
|---|---|---|
| markets, tokens | Gamma `/markets?condition_ids=…&closed=true` (**`closed=true` required**: closed markets silently return `[]` without it) | D2 G-MARKET |
| resolution tx + instant | chain: CTF `ConditionResolution(conditionId)` log (tx, block, block ts, payouts). **Never** `v2/resolutions.transaction_hash`, which is the creation tx | census r2 M3/M4 |
| trade-close / T_ref | Gamma `closedTime`. 0 s delta to chain on control. Per-market deltas: G1 A21 run | census r2 C |
| fills | `v2/trades?condition=` walks `taker` and `all`, cursor, limit 1000 | census D1/D2, r2 B |
| holders | `v2/holders` | census D3 |
| positions | `v2/positions` (status semantics open, F6 → G3) | census D4 |
| per-wallet lifecycle (first trade, REDEEM for S8) | `v2/activity?user=` (per wallet only) | census D5b |
| entry prior (S5), price at time (S4) | fill `price` from the taker walk. CLOB `prices-history` with explicit `startTs/endTs` (≤13 d) secondary | census D2, C2 |
| funding edges | ERC-20 Transfer into proxy for USDC.e `0x2791…4174`, USDC `0x3c49…3359`, pUSD `0xc011…2dfb`, via Dune or tenderly `eth_getLogs` (≤2000-block windows) | census collateral, r2 §E |
| event instant (S4) | Planner event table | plan |

## 2. Schema (DuckDB, derived, one file per SNAP)

```sql
-- provenance: one row per FETCH. snapshot_id = sha256 of the exact bytes written.
-- Identical bytes from different requests (e.g. empty pages) share snapshot_id, hence key on (snap, path).
snapshots(snapshot_id TEXT, snap TEXT, source TEXT, request_name TEXT, page INT,
          endpoint TEXT, params_json TEXT, fetched_at TIMESTAMPTZ, http_status INT,
          chain_block BIGINT, bytes BIGINT, path TEXT,
          PRIMARY KEY (snap, path))                                   -- [D2 deviation 2, PENDING RULING]

markets(condition_id TEXT PK, gamma_id TEXT, event_id TEXT, question TEXT, neg_risk BOOLEAN,
        created_at TIMESTAMPTZ, accepting_orders_at TIMESTAMPTZ,
        closed_at TIMESTAMPTZ,                  -- Gamma closedTime
        resolved_outcome_index INT,             -- Gamma outcomePrices >= 0.99; cross-checked vs chain payouts
        resolution_tx TEXT, resolution_block BIGINT, resolution_ts TIMESTAMPTZ,  -- chain ConditionResolution only
        gamma_volume_shares DOUBLE,             -- SHARES (census r2 M1), never labelled $
        snapshot_id TEXT)
tokens(token_id TEXT PK, condition_id TEXT, outcome TEXT, outcome_index INT)

trades(snapshot_id TEXT, seq INT, walk TEXT,     -- walk: 'taker' | 'all'
       tx_hash TEXT, log_index INT,              -- log_index NULL for API rows
       condition_id TEXT, token_id TEXT, proxy_wallet TEXT, side TEXT,
       size DOUBLE,                              -- shares
       price DOUBLE,                             -- USDC per share
       ts TIMESTAMPTZ, source TEXT,              -- 'dataapi_v2' | 'dune' | 'chain'
       PRIMARY KEY (snapshot_id, walk, seq))     -- [D2 deviation 1, PENDING RULING]; API rows NOT deduplicated

holders_snap(snapshot_id, seq, token_id, proxy_wallet, amount, outcome_index, PK(snapshot_id, seq))
positions_snap(snapshot_id, seq, proxy_wallet, token_id, condition_id, current_size, total_size,
               avg_price, total_cost_usdc, realized_pnl, total_pnl, status, PK(snapshot_id, seq))
api_resolutions(snapshot_id, condition_id, question_id, status, was_disputed, price,
                api_tx_hash,                     -- = market CREATION tx (census r2 M4); kept as returned
                last_update_ts, PK(snapshot_id, condition_id))
activity(snapshot_id, seq, proxy_wallet, ts, type, condition_id, token_id,
         side, size, usdc_size, price, tx_hash, PK(snapshot_id, seq))       -- not built yet

wallets(proxy_wallet TEXT PK,
        first_trade_ts TIMESTAMPTZ,              -- v2/activity earliest
        first_funding_ts TIMESTAMPTZ,            -- funding_edges earliest inbound collateral
        wallet_kind TEXT,                        -- eth_getCode: 'eip1167_proxy' | 'safe' | 'eoa' | NULL
        is_control BOOLEAN,                      -- member of a positive/negative control list (bypasses prefilter)
        passed_prefilter BOOLEAN,                -- G4 req 2: TRUE if the prefilter rule selected it (controls: actual result, recorded even though bypassed)
        profiled BOOLEAN)                        -- activity + funding walks done (= passed_prefilter OR is_control)

funding_edges(tx_hash, log_index, block, ts, token,   -- token: 'USDC.e' | 'USDC' | 'pUSD'
              from_addr, to_addr, amount, hop, from_entity, snapshot_id,
              PRIMARY KEY (tx_hash, log_index))
entities(address PK, label, kind,                -- 'cex' | 'bridge' | 'polymarket_infra' | 'wrapper'
         source_url, verified_on_polygon BOOLEAN, verified_at)

signals(run_id TEXT, proxy_wallet TEXT,
        scope TEXT,                              -- condition_id | 'event:<id>' | 'all'
        signal_id TEXT,                          -- 'S1'..'S8'
        raw_value DOUBLE,                        -- NULL when NA
        component DOUBLE,                        -- in [0,1]; NULL when NA (never 0 for NA)
        na_reason TEXT,                          -- G4 req 3: NULL unless NA, e.g. 'n_resolved<min', 'not_profiled', 'no_funding_found'
        evidence_json TEXT,
        PRIMARY KEY (run_id, proxy_wallet, scope, signal_id),
        CHECK ((raw_value IS NULL) = (na_reason IS NOT NULL)))
scores(run_id TEXT, proxy_wallet TEXT,
       scope TEXT,                               -- G4 req 1: MUST include 'all' (one total per wallet over all 171 M include markets), plus per-condition and per-event
       total DOUBLE,                             -- Σ weight·component over non-NA signals; NA contributes 0 and is listed
       n_signals_na INT,
       PRIMARY KEY (run_id, proxy_wallet, scope))
runs(run_id TEXT PK, started_at, finished_at,
     weights_path TEXT, weights_sha256 TEXT, code_sha TEXT,
     snapshot_ids_json TEXT,
     prefilter_rule TEXT,                        -- G4 req 2: exact rule text, e.g. 'S3 low-odds stake >= X USDC at price < 0.20'
     prefilter_cut_json TEXT,                    -- threshold(s) + counts: population, passed, controls bypassed
     scope_limit_text TEXT)                      -- shown in UI: 'wallets below S3 cut not profiled'
ingest_checks(snap, condition_id, check_id, passed, detail_json, PK(snap, condition_id, check_id))
```

Enforcement (code or test, not comments):

| rule | where enforced | break-tested |
|---|---|---|
| G-MARKET: exactly 1 Gamma market per condition, else abort | `radar/ingest.py` `fetch_market` | yes (D2) |
| G-TRADES: 0 taker rows with Gamma volume > 0, abort | `check_trades_gate` | yes (D2) |
| G-A2B: `all` rows == `taker` rows, abort (ignored param) | `check_trades_gate` | yes (D2) |
| identity columns absent from every derived table | `tests/test_control_gates.py::test_healthy_market_loads` against `config.IDENTITY_FIELDS` | fired for real (D2: `snapshots.name`) |
| trades key survives identical pages | regression test | yes (D2) |
| NA ⇔ raw_value NULL | table CHECK constraint | to test at G4 |
| weights hash recomputed at score time; run refuses on mismatch | scorer (G4) | to test at G4 |
| scope='all' row per scored wallet | G4 test: count(scope='all') = count(distinct scored wallets) | to test at G4 |

## 3. Ingest flow

1. **Market set** = M include (171). Gamma lookup per condition (`closed=true`). G-MARKET.
2. **Fills**: `taker` walk + `all` walk. G-TRADES, G-A2B.
3. **Holders, positions, api_resolutions**.
4. **Resolution from chain**: `ConditionResolution` per condition → `markets.resolution_*`. Cross-check payouts against Gamma `outcomePrices`. Same method as G1 A21 (`app/tools/a21_p2.py`).
5. **Wallet universe** = distinct `proxy_wallet` over the `all` walk.
   - Prefilter rule (to be fixed before first score; QA rules) sets `passed_prefilter`.
   - Controls get `is_control=TRUE` and are profiled regardless.
6. **Per-wallet walks** for `profiled` wallets: `v2/activity`, funding (3 tokens, ≤3 hops).
   - Stop at `entities` kind cex/bridge. Pass through `wrapper` / `polymarket_infra`.
7. **Signals** S1–S8 per scope (condition, event, all). NA rows carry `na_reason`.
8. **Scores**. Weights come from one hashed config, recorded in `runs`.
9. **Reconcile gate**: per market, Σ taker size vs Gamma volume (U-UNITS, tolerance 1%, recorded); Dune fill count when the key lands. Gaps are shown, not hidden.

S-notes:
- S1 uses `first_funding_ts` / `first_trade_ts`, never nonce.
- S5 prior = fill price. Small n → NA (`na_reason='n_resolved<min'`).
- S8 uses `activity` REDEEM rows (profiled wallets only; others NA `'not_profiled'`).

## 4. Reuse

Unchanged from r1 §4, including the ban on the synthetic random-price fallback in the old `2_fetch_prices.py`.

## 5. Dune SQL

Unchanged from r1 §5 (UNVERIFIED columns; probe P0 first). Q1 control count to compare:
- API taker walk 86,991 fills for the Feb-28 condition.
- Dune `market_trades` is expected to be per-fill. Compare fill counts, not the 217,997 `all` rows.

## 6. Open

| id | item | owner |
|---|---|---|
| O1 | residual 802.85 shares Gamma − Σ taker size (Feb-28): small, non-blocking | Developer |
| O2 | F6 holders ≠ positions (328) | Tester-2 at G3 |
| O3 | UMA propose/settle instant (not in the resolution tx) | unmeasured |
| O4 | tenderly / quiknode / data-api rate limits | Developer, before bulk |
| O5 | D2 deviations 1–2 (keys) + one-DB-per-SNAP | **QA ruling pending** |
| O6 | pUSD wrap/mint contract → `entities` kind `wrapper` | Developer |
| O7 | a fill 76 s after on-chain resolution on the control market (last fill 09:32:33Z vs resolution 09:31:17Z) | observed, not analysed |
