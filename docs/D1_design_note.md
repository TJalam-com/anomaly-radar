# D1 design note — data model + ingest flow (pre-code, for QA ruling)

Developer `local_345bbd13-bdb9-4843-9f01-f60c956a317d`, 2026-09-26. Evidence: `results/D1_endpoint_census_2026-09-26.md`.
Nothing built yet. Every decision below cites a census row or is marked INFERENCE.

## 0. Decisions that need a QA ruling before code

1. **Trades source of truth.** Proposal: Data API `v2/trades?taker_only=false` for Phase 1, reconciled against a chain source before any P&L or win-rate figure is published.
   - Why: census F5 found a 12.9% notional gap vs Gamma.
   - Chain source: Dune `polymarket_polygon.market_trades`, or OrderFilled logs.
2. **No natural key on API trades.** Census F3/identical-rows: genuine repeated fills produce identical rows.
   - Proposal: surrogate key `(snapshot_id, seq)` and keep duplicates.
   - The chain key `(tx_hash, log_index)` is added once we have a chain source.
3. **Archive-capable RPC is needed** for funding (S1, S6).
   - Census C3: the default publicnode RPC returns null for Feb-2026 receipts.
   - Options: Dune (key pending), drpc public (worked; limits unmeasured), or Etherscan V2 key.
   - Principal/QA to choose. Until then, funding runs through Dune only.
4. **Schema-change policy.** The DuckDB analytics tables are *derived*: rebuilt from hashed parquet snapshots, no migrations. Only SQLite app state (watchlists, UI prefs; none in Phase 1) would need migrations.
   - So a derived-schema change = rebuild, not migrate.
   - Proposal: no Alembic in Phase 1.

## 1. Data needs → source (measured)

| need | source | census row |
|---|---|---|
| markets, tokens, resolution time | Gamma `/events/{id}`, `/markets/{id}`; `v2/resolutions` for resolution tx + ts | G2, G3, D6 |
| all fills per market | `v2/trades?condition=&taker_only=false` (cursor, limit 1000) | D2 |
| current holders per outcome | `v2/holders` | D3 |
| per-wallet cost basis / P&L | `v2/positions` (status semantics unresolved, F6) | D4 |
| per-wallet lifecycle (first trade, REDEEM for S8) | `v2/activity?user=` (per wallet only) | D5b |
| minute price for S4/S5 entry prior | fill prices from D2 (primary); CLOB `prices-history` with explicit `startTs/endTs` windows ≤13 d (secondary) | D2, C2 |
| funding edges | ERC-20 Transfer into proxy for **USDC.e `0x2791…4174`, USDC `0x3c49…3359`, pUSD `0xc011…2dfb`** via Dune or archive RPC | collateral table |
| news / event instant for S4 | TBD by Planner (brief §4) | — |

## 2. Schema (DuckDB, derived; every row carries provenance)

```sql
-- provenance: one row per fetched page/file; every fact table references it
snapshots(snapshot_id TEXT PK,          -- sha256 of raw bytes
          source TEXT,                  -- 'gamma' | 'dataapi_v2' | 'clob' | 'dune' | 'rpc'
          endpoint TEXT, params_json TEXT,
          fetched_at TIMESTAMPTZ, http_status INT,
          chain_block BIGINT NULL,      -- v2/status max_synced_block or RPC block at fetch
          bytes BIGINT, path TEXT)      -- app/data/snapshots/...parquet|json

markets(condition_id TEXT PK, gamma_id TEXT, event_id TEXT, question TEXT, neg_risk BOOL,
        created_at TIMESTAMPTZ, accepting_orders_at TIMESTAMPTZ,
        closed_at TIMESTAMPTZ, resolved_outcome_index INT NULL,
        resolution_tx TEXT NULL, resolution_ts TIMESTAMPTZ NULL,
        gamma_volume DOUBLE, snapshot_id TEXT)
tokens(token_id TEXT PK, condition_id TEXT, outcome TEXT, outcome_index INT)

wallets(proxy_wallet TEXT PK,
        first_trade_ts TIMESTAMPTZ NULL,        -- from v2/activity (earliest)
        first_funding_ts TIMESTAMPTZ NULL,      -- from funding_edges (earliest inbound collateral)
        wallet_kind TEXT NULL,                  -- 'eip1167_proxy' | 'safe' | 'eoa' | NULL (eth_getCode)
        snapshot_id TEXT)                       -- NO names/pseudonyms/bios stored (brief §6)

trades(snapshot_id TEXT, seq INT,               -- surrogate PK (no natural key in API)
       tx_hash TEXT, log_index INT NULL,        -- log_index filled only from chain source
       condition_id TEXT, token_id TEXT, proxy_wallet TEXT,
       side TEXT, size DOUBLE, price DOUBLE, ts TIMESTAMPTZ,
       is_taker BOOL NULL,                      -- = present in taker-only walk
       source TEXT,                             -- 'dataapi_v2' | 'dune' | 'chain'
       PRIMARY KEY (snapshot_id, seq))

holders_snap(snapshot_id, token_id, proxy_wallet, amount)            -- point-in-time
positions_snap(snapshot_id, proxy_wallet, token_id, avg_price, total_cost_usdc,
               realized_pnl, total_pnl, status)
activity(snapshot_id, seq, proxy_wallet, ts, type, condition_id, token_id,
         side, size, usdc_size, price, tx_hash)                      -- TRADE/REDEEM/SPLIT/MERGE...

funding_edges(tx_hash TEXT, log_index INT,       -- natural PK from chain
              block BIGINT, ts TIMESTAMPTZ,
              token TEXT,                        -- 'USDC.e' | 'USDC' | 'pUSD'
              from_addr TEXT, to_addr TEXT, amount DOUBLE,
              hop INT,                           -- 0 = direct into proxy
              from_entity TEXT NULL,             -- registry label (CEX/bridge/wrapper) or NULL
              snapshot_id TEXT,
              PRIMARY KEY (tx_hash, log_index))
entities(address TEXT PK, label TEXT, kind TEXT, -- 'cex'|'bridge'|'polymarket_infra'|'wrapper'
         source_url TEXT, verified_on_polygon BOOL, verified_at TIMESTAMPTZ)

signals(run_id TEXT, proxy_wallet TEXT, scope TEXT,   -- scope = condition_id | event_id | 'all'
        signal_id TEXT,                               -- 'S1'..'S8'
        raw_value DOUBLE, component DOUBLE,           -- component in [0,1]
        evidence_json TEXT,                           -- tx hashes, prices, p-values used
        PRIMARY KEY (run_id, proxy_wallet, scope, signal_id))
scores(run_id TEXT, proxy_wallet TEXT, scope TEXT, total DOUBLE,
       PRIMARY KEY (run_id, proxy_wallet, scope))
runs(run_id TEXT PK, started_at, finished_at,
     weights_path TEXT, weights_sha256 TEXT, code_sha TEXT,
     snapshot_ids_json TEXT)                          -- every figure traces to snapshots + config
```

Enforcement, not comments:
- `runs.weights_sha256` is recomputed at score time. A run refuses to start if the weights file hash ≠ the value recorded in the run.
- The ingest writer computes `snapshot_id = sha256(bytes)` on the bytes it writes, never from a separate read.
- UI queries join `scores → runs → snapshots` so every displayed number can show its instant and source (brief §6).

## 3. Ingest flow (Phase 1)

1. **Market set**: Gamma `/events/{id}` for the QA/Planner-approved event list. Store raw JSON as a snapshot, then fill `markets` and `tokens`.
2. **Fills**: per condition, cursor-walk `v2/trades?taker_only=false`. Also walk the taker-only default to set `is_taker`.
   - Raw pages go to `app/data/snapshots/dataapi_v2/trades/<cond>/<sha>.json`, then to parquet.
   - Control: abort the market if the walk returns 0 rows while Gamma `volume > 0`.
3. **Holders + positions + resolutions** per condition (one pass each).
4. **Candidate wallets**: every wallet that holds or trades in the set (Feb-28 market alone: 22,933 wallets).
   - Per-wallet `v2/activity` walks happen only for wallets passing a cheap prefilter (e.g. S3 low-odds size). Walking all ~23k per market is ~23k calls. INFERENCE: too slow without measuring rate limits.
5. **Funding** (S1, S6) for candidate wallets:
   - Inbound Transfer logs for the 3 collateral tokens, up to 3 hops.
   - Stop at `entities` of kind cex/bridge.
   - **Pass through** kind `wrapper` (the pUSD mint path) and `polymarket_infra`.
   - Source: Dune when the key lands, else an archive RPC (decision 0.3).
6. **Signals**: SQL/Python over DuckDB, one function per S-id. Each writes `raw_value`, `component` and `evidence_json`.
7. **Score** = Σ weight·component, with weights from a single `app/config/weights.toml`. The `runs` row records the hash.
8. **Reconcile gate**: for each market compare Σ(size·price) against Gamma volume and against the Dune trades count once available. Publish the gap in the UI and do not hide it.

S-signal notes forced by the census:
- **S1**: use `first_funding_ts` or `first_trade_ts`, **not nonce**. A proxy nonce is 1 on a wallet active since 2024.
- **S5**: prior = entry price per fill, from D2.
- **S8**: REDEEM rows from `activity`.

## 4. Reuse

### 4a. Old football code (recovered from Read tool results)

- Write/Edit inputs for these files do not exist in either transcript. Only `config.py` Edits exist, with no base.
- Content was rebuilt from Read tool results: `1_fetch_markets.py` 348 lines, `2_fetch_prices.py` 329 lines, `config.py` 92 lines.
- The last Read chunks hit their `limit` exactly, so the **files may be truncated**. `2_fetch_prices.py` visibly ends mid-`main()`.
- The two transcripts gave byte-identical output (the second session is a fork of the first).
- Recovered copies in the Developer scratchpad are redacted aggressively: any line naming key/token/secret is dropped. Credentials were not read.

| part | reusable? | why |
|---|---|---|
| `2_fetch_prices.dune_execute/dune_poll` (POST `/api/v1/sql/execute`, poll `/execution/{id}/results`) | **yes, pattern** | small, generic; rewrite with httpx + backoff + snapshot hashing |
| `2_fetch_prices._dune_batch_query` (IN-list of `uint256 'asset_id'`, time window) | **yes, pattern** | same table we need; switch key to `condition_id` |
| `2_fetch_prices` synthetic random-price fallback (`random.Random`, `rng.uniform`) | **NO — ban** | fabricates prices; violates brief §6 |
| `2_fetch_prices.build_price_series` minute forward-fill | idea only | football kickoff-relative; S4 needs event-relative |
| `1_fetch_markets.fetch_page` (Gamma `/markets?closed&tag&limit&offset`) | idea only | we use `/events/{id}` for a fixed set |
| `1_fetch_markets.extract_outcome` (`outcomePrices` ≥0.99 → Yes) | **yes** | same Gamma field; add `v2/resolutions` cross-check |
| EPL regex / slug team map, `config.GAMMA_SCAN_RANGES` | no | football only |
| `config.py` dotenv pattern | yes | trivial |

### 4b. pselamy/polymarket-insider-tracker

- MIT, `Copyright (c) 2026 Patrick Selamy`, commit `302c6b99` read 2026-09-26.
- Read by a Developer subagent via shallow clone to the scratchpad. I did not re-read every line myself.

| module | vendor? | reason (direct evidence from read) |
|---|---|---|
| `profiler/entity_data.py` + `entities.py` | **vendor as seed**, keep LICENSE + attribution header | pure Python, no deps. But addresses are labelled from Etherscan/Arkham and some look like Ethereum-mainnet labels. Every entry gets `verified_on_polygon=false` until checked. No Polymarket-infra or pUSD-wrapper entries, so we add those |
| `profiler/funding.py` | **ideas only, rewrite** | (a) looks back only 80k blocks (~44 h), so the funder is wrong for older wallets; (b) queries bridged USDC first and native only if none, so it misses earlier native flows; (c) no pUSD; (d) pulls in web3 + Redis-coupled client. We keep: multi-hop walk, stop-at-CEX/bridge rule, hop-based suspiciousness idea |
| `detector/fresh_wallet.py` | ideas only | freshness = nonce ≤5, which is useless on proxies (census). Thresholds ($1000 min, 48 h) are usable as S1 config defaults with attribution |
| `detector/size_anomaly.py` | ideas only | 2% of 24 h volume, 5% of book depth, niche < $50k. Redis-coupled. Category from title keywords |
| `detector/sniper.py` | **no** | features degenerate (md5 market hash, delta_hours ≪ eps); not wired into their pipeline (their gap register G-026) |
| `detector/scorer.py` | **no** | live pipeline max ≈ 0.528 < their 0.80 threshold (per subagent calc from constants); funding/sniper absent |
| ingest/storage/alerter/pipeline | no | Postgres + Redis + alembic infra; unversioned data-api `/trades` path (possibly v1) |

**Not read in D1**: suislanchez/polymarket-insider-detector (MIT; binomial p-value for S5), 0xinsider/research (CC BY 4.0; edge = win rate − avg price). Candidates for S5 method, D2.

## 5. Dune SQL drafts (NOT run — key not in `app/.env`)

Column names below are **UNVERIFIED**. The first execution must be the probe, and the drafts get fixed to its output.

```sql
-- P0 probe: schema + V2-exchange coverage (does Dune index post-2026-04-28 fills?)
SELECT * FROM polymarket_polygon.market_trades LIMIT 5;
SELECT date_trunc('month', block_time) AS m, count(*) AS n
FROM polymarket_polygon.market_trades
WHERE block_time >= TIMESTAMP '2026-01-01'
GROUP BY 1 ORDER BY 1;

-- Q1 trades by condition id (Feb-28 control first; expect order of 10^5 rows, cf. census D2 = 217,997 API rows)
SELECT block_time, block_number, tx_hash, evt_index,
       condition_id, asset_id, price, amount, shares, maker, taker
FROM polymarket_polygon.market_trades
WHERE condition_id = 0x3488f31e6449f9803f99a8b5dd232c7ad883637f1c86e6953305a2ef19c77f20
ORDER BY block_time, evt_index;

-- Q2 collateral transfers INTO one wallet (all three tokens), full history
SELECT block_time, block_number, tx_hash, evt_index, contract_address, "from", "to", amount_raw
FROM erc20_polygon.evt_Transfer
WHERE "to" = {{wallet}}
  AND contract_address IN (
      0x2791bca1f2de4661ed88a30c99a7a9449aa84174,  -- USDC.e
      0x3c499c542cef5e3811e1192ce70d8cc03d5c3359,  -- USDC native
      0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb)  -- pUSD (census: post-migration collateral)
ORDER BY block_time
LIMIT 1000;
```

Control for Q1/Q2: the Feb-28 condition must return > 0 rows, and wallet `0xec753c8b707c4dbc22d49a1e2bb4d1bec63ff4e0` must show its USDC.e funding before its 2026-02-19 fill. Otherwise the query is void.

## 6. Not verified / open

- F5 volume gap and F6 holders≠positions: unexplained. Phase-1 figures carry a caveat until resolved.
- Rate limits of data-api v2 and drpc: not measured.
- pUSD wrap/unwrap path (the contract that mints pUSD from USDC): not identified, so the `wrapper` entity list is empty so far.
- Phase-1 event list: I used 5 events from one search. It is not approved.
- The subagent's read of the pselamy repo: I did not independently re-read `funding.py` / `scorer.py` line by line.
