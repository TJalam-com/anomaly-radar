"""Derived DuckDB schema, one database file per SNAP (data/snapshots/SNAP-NNN/radar.duckdb) (design note D1 §2, cleared at 490a2a2d…2f6f, plus QA D2 rulings:
walk label per trade row instead of is_taker; identity fields never stored). Rebuilt from snapshots, not migrated."""

DDL = [
    """CREATE TABLE IF NOT EXISTS snapshots(
        snapshot_id TEXT, snap TEXT, source TEXT, request_name TEXT, page INT,
        endpoint TEXT, params_json TEXT, fetched_at TIMESTAMPTZ, http_status INT,
        chain_block BIGINT, bytes BIGINT, path TEXT,
        -- one row per fetch; identical bytes from different requests (e.g. empty pages) share snapshot_id
        PRIMARY KEY (snap, path))""",
    """CREATE TABLE IF NOT EXISTS markets(
        condition_id TEXT PRIMARY KEY, gamma_id TEXT, event_id TEXT, question TEXT, neg_risk BOOLEAN,
        created_at TIMESTAMPTZ, accepting_orders_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
        resolved_outcome_index INT, resolution_tx TEXT, resolution_ts TIMESTAMPTZ,
        gamma_volume_shares DOUBLE, snapshot_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS tokens(
        token_id TEXT PRIMARY KEY, condition_id TEXT, outcome TEXT, outcome_index INT)""",
    # API rows: NOT deduplicated (QA ruling). walk = 'taker' (default taker_only=true) | 'all' (taker_only=false).
    # walk is in the key: a byte-identical page served to both walks would otherwise collide on (snapshot_id, seq).
    """CREATE TABLE IF NOT EXISTS trades(
        snapshot_id TEXT, seq INT, walk TEXT, tx_hash TEXT, log_index INT,
        condition_id TEXT, token_id TEXT, proxy_wallet TEXT, side TEXT,
        size DOUBLE, price DOUBLE, ts TIMESTAMPTZ, source TEXT,
        PRIMARY KEY (snapshot_id, walk, seq))""",
    """CREATE TABLE IF NOT EXISTS holders_snap(
        snapshot_id TEXT, seq INT, token_id TEXT, proxy_wallet TEXT, amount DOUBLE, outcome_index INT,
        PRIMARY KEY (snapshot_id, seq))""",
    """CREATE TABLE IF NOT EXISTS positions_snap(
        snapshot_id TEXT, seq INT, proxy_wallet TEXT, token_id TEXT, condition_id TEXT,
        current_size DOUBLE, total_size DOUBLE, avg_price DOUBLE, total_cost_usdc DOUBLE,
        realized_pnl DOUBLE, total_pnl DOUBLE, status TEXT,
        PRIMARY KEY (snapshot_id, seq))""",
    # v2/resolutions as returned. api_tx_hash is the market-CREATION tx (census r2 M4), not the resolution.
    """CREATE TABLE IF NOT EXISTS api_resolutions(
        snapshot_id TEXT, condition_id TEXT, question_id TEXT, status TEXT, was_disputed BOOLEAN,
        price TEXT, api_tx_hash TEXT, last_update_ts TIMESTAMPTZ,
        PRIMARY KEY (snapshot_id, condition_id))""",
    """CREATE TABLE IF NOT EXISTS ingest_results(
        condition_id TEXT PRIMARY KEY, status TEXT, reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS ingest_checks(
        snap TEXT, condition_id TEXT, check_id TEXT, passed BOOLEAN, detail_json TEXT,
        PRIMARY KEY (snap, condition_id, check_id))""",
]


def create(con):
    for stmt in DDL:
        con.execute(stmt)
