"""Scoring views over a SNAP + its step-4 side-car (design notes r3 Δ1/Δ2, r4 R3/R4, QA A10).

markets_r     = markets joined with chain resolution (resolution_ts, void, chain winner, t_ref)
signal_fills  = trades with ts < resolution_ts (strict; post-resolution and same-second fills excluded)
Pre-run assert (A10): every in-scope condition must have a chain resolution_ts, else ResolutionMissing.
"""
from pathlib import Path


class ResolutionMissing(RuntimeError):
    pass


def build_scoring_views(con, snap_dir, scope_conditions: list[str] | None = None) -> None:
    snap_dir = Path(snap_dir)
    D = (snap_dir / "derived").as_posix()
    S4 = snap_dir / "step4" / "resolutions_chain.parquet"
    if not S4.exists():
        raise ResolutionMissing(f"no step-4 side-car at {S4}; run radar.step4 first")
    con.execute(f"""CREATE OR REPLACE VIEW markets_r AS
        SELECT m.*, to_timestamp(r.resolution_ts_unix) AS chain_resolution_ts, r.resolution_block AS chain_resolution_block,
               r.resolution_tx AS chain_resolution_tx, r.void, r.chain_winner_index, to_timestamp(r.t_ref_unix) AS t_ref
        FROM '{D}/markets.parquet' m LEFT JOIN '{S4.as_posix()}' r USING (condition_id)""")
    scope = scope_conditions if scope_conditions is not None else \
        [c for (c,) in con.execute("SELECT condition_id FROM markets_r").fetchall()]
    missing = con.execute("SELECT condition_id FROM markets_r WHERE chain_resolution_ts IS NULL AND list_contains(?, condition_id)",
                          [scope]).fetchall()
    absent = set(scope) - {c for (c,) in con.execute("SELECT condition_id FROM markets_r").fetchall()}
    if missing or absent:
        raise ResolutionMissing(f"{len(missing)} in-scope conditions lack chain resolution_ts, {len(absent)} not in markets: "
                                f"{[c for (c,) in missing][:5]} {sorted(absent)[:5]}")
    con.execute(f"""CREATE OR REPLACE VIEW signal_fills AS
        SELECT t.* FROM '{D}/trades.parquet' t JOIN markets_r m USING (condition_id)
        WHERE t.ts < m.chain_resolution_ts""")
