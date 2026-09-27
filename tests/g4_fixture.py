"""Tiny synthetic world for G4 signal tests. Builds exactly the tables radar.signals reads."""
from datetime import datetime, timezone

import duckdb
import pyarrow as pa

RES = 1_772_000_000  # resolution ts of every fixture market (unix)
P_DEFAULT = dict(p_low=0.20, pre_min_stake=1000, s1_min_stake=1000, s1_fresh_h=48, s1_stale_h=720,
                 s3_min_stake=1000, s3_cap_usdc=100000, s4_lead_h=24, s4_min_stake=1000, s4_cap_usdc=50000,
                 s5_full_at=6.0, s5_min_bets=3, s6_max_hops=3, s6_full_at=5, s6_fanout_max=20,
                 s7_min_stake=1000, s7_window_s=600, s7_full_at=5, s8_quick_h=24, s8_dormant_d=30,
                 s6_hop_breadth=3, _t_asof_unix=RES + 10_000_000)


def T(u):
    return datetime.fromtimestamp(u, tz=timezone.utc)


def W(n):
    return "0x" + f"{n:040x}"


class World:
    def __init__(self):
        self.markets = {}   # cond -> dict(winner, void, res)
        self.trades = []    # (cond, token, wallet, side, size, price, ts)
        self.anchors = []   # (cond, anchor_ts, precision_s, na)
        self.profile = {}   # wallet -> dict
        self.redeems = []   # (wallet, cond, ts, shape)
        self.trade_after = []  # (wallet, cond, next_trade_ts)
        self.transfers = []  # (wallet, direction, hop, counterparty[, via, selected, stop_listed, first_ts, first_block])
        self.lookups = {}    # S6 v3: X -> dict(status, capped, cap_block, from_block, to_block, subranges, first_blocks)
        self.expansions = None  # None -> complete hop-1 rows for every profiled wallet; else list of dict rows
        self.stop = []
        self.bypass = []
        self.scopes = {}    # scope -> [conds]

    def market(self, cond, winner=0, void=False, res=RES):
        self.markets[cond] = dict(winner=None if void else winner, void=void, res=res)
        self.scopes.setdefault("all", []).append(cond)
        self.scopes.setdefault(cond, [cond])
        return self

    def tok(self, cond, idx):
        return f"{cond}-t{idx}"

    def fill(self, cond, wallet, side, size, price, ts, idx=0):
        self.trades.append((cond, self.tok(cond, idx), wallet, side, size, price, ts))
        return self

    def build(self):
        con = duckdb.connect()
        con.execute("SET threads=1")
        m = self.markets
        con.register("_m", pa.table({"condition_id": list(m), "void": [m[c]["void"] for c in m],
                                     "chain_winner_index": pa.array([m[c]["winner"] for c in m], pa.int64()),
                                     "chain_resolution_ts": [T(m[c]["res"]) for c in m]}))
        con.execute("CREATE TABLE markets_r AS SELECT * FROM _m")
        toks = [(self.tok(c, i), c, i) for c in m for i in (0, 1)]
        con.register("_k", pa.table({"token_id": [t[0] for t in toks], "condition_id": [t[1] for t in toks],
                                     "outcome_index": [t[2] for t in toks]}))
        con.execute("CREATE TABLE tokens_v AS SELECT * FROM _k")
        tr = self.trades or [("x", "x", W(0), "BUY", 0.0, 0.5, 0)]
        con.register("_t", pa.table({"condition_id": [t[0] for t in tr], "token_id": [t[1] for t in tr],
                                     "proxy_wallet": [t[2] for t in tr], "side": [t[3] for t in tr],
                                     "size": [float(t[4]) for t in tr], "price": [float(t[5]) for t in tr],
                                     "ts": [T(t[6]) for t in tr], "walk": ["all"] * len(tr)}))
        con.execute("CREATE TABLE trades_all_any AS SELECT * FROM _t WHERE condition_id <> 'x'")
        con.execute("CREATE VIEW signal_fills AS SELECT t.* FROM trades_all_any t JOIN markets_r m USING (condition_id) WHERE t.ts < m.chain_resolution_ts")
        sm = [(s, c) for s, cs in self.scopes.items() for c in cs]
        con.register("_s", pa.table({"scope": [x[0] for x in sm], "condition_id": [x[1] for x in sm]}))
        con.execute("CREATE TABLE scope_map AS SELECT * FROM _s")
        con.execute("CREATE TABLE anchors(condition_id TEXT, anchor_ts TIMESTAMPTZ, anchor_precision_s BIGINT, na_reason TEXT)")
        for c, a, p, na in self.anchors:
            con.execute("INSERT INTO anchors VALUES (?,?,?,?)", [c, T(a) if a is not None else None, p, na])
        con.execute("""CREATE TABLE wallet_profile(proxy_wallet TEXT, first_trade_ts TIMESTAMPTZ, first_funding_ts TIMESTAMPTZ,
            lifetime_volume_usdc DOUBLE, activity_status TEXT, stats_status TEXT, transfers_status TEXT, t_snap TIMESTAMPTZ,
            funding_truncated BOOLEAN, lower_bound_ts TIMESTAMPTZ)""")
        for w, p in self.profile.items():
            con.execute("INSERT INTO wallet_profile VALUES (?,?,?,?,?,?,?,?,?,?)", [
                w, T(p["ftt"]) if p.get("ftt") is not None else None, T(p["fft"]) if p.get("fft") is not None else None,
                p.get("vol"), p.get("act", "ok"), p.get("stats", "ok"), p.get("tr", "ok"),
                T(p["tsnap"]) if p.get("tsnap") is not None else None, p.get("trunc", False),
                T(p["lb_ts"]) if p.get("lb_ts") is not None else None])
        con.execute("CREATE TABLE redeems(proxy_wallet TEXT, condition_id TEXT, ts TIMESTAMPTZ, shape TEXT)")
        for w, c, t, s in self.redeems:
            con.execute("INSERT INTO redeems VALUES (?,?,?,?)", [w, c, T(t), s])
        con.execute("CREATE TABLE trade_after(proxy_wallet TEXT, condition_id TEXT, next_trade_ts TIMESTAMPTZ)")
        for w, c, t in self.trade_after:
            con.execute("INSERT INTO trade_after VALUES (?,?,?)", [w, c, T(t) if t is not None else None])
        con.execute("""CREATE TABLE transfers(proxy_wallet TEXT, direction TEXT, hop INT, counterparty TEXT, via TEXT,
            selected BOOLEAN, stop_listed BOOLEAN, first_ts TIMESTAMPTZ, first_block BIGINT)""")
        for x in self.transfers:
            x = list(x) + [None] * (9 - len(x))
            via = x[4] if x[4] is not None else (x[0] if x[2] == 1 else None)
            con.execute("INSERT INTO transfers VALUES (?,?,?,?,?,?,?,?,?)",
                        [x[0], x[1], x[2], x[3], via, bool(x[5]), bool(x[6]), T(x[7]) if x[7] is not None else None, x[8]])
        con.execute("""CREATE TABLE lookups(address TEXT, status TEXT, capped BOOLEAN, cap_block BIGINT, from_block BIGINT,
            to_block BIGINT, subranges_json TEXT)""")
        con.execute("CREATE TABLE lookup_y(address TEXT, y TEXT, first_block BIGINT)")
        import json as _json
        for a, r in self.lookups.items():
            con.execute("INSERT INTO lookups VALUES (?,?,?,?,?,?,?)", [a, r.get("status", "ok"), r.get("capped", False), r.get("cap_block"),
                        r.get("from_block", 0), r.get("to_block", 10_000), _json.dumps(r.get("subranges", [{"from": 0, "to": r.get("to_block", 10_000), "status": "ok"}]))])
            for i, fb in enumerate(r.get("first_blocks", [])):
                con.execute("INSERT INTO lookup_y VALUES (?,?,?)", [a, f"y{i}", fb])
        con.execute("""CREATE TABLE expansions(proxy_wallet TEXT, direction TEXT, hop INT, parent TEXT, address TEXT, status TEXT,
            n_logs BIGINT, to_block_ts BIGINT, gap_free BOOLEAN, has_child_edges BOOLEAN)""")
        exps = self.expansions if self.expansions is not None else [
            dict(proxy_wallet=w, direction=d, hop=1, address=w) for w in list(self.profile) + list(self.bypass) for d in ("in", "out")]
        for e in exps:
            con.execute("INSERT INTO expansions VALUES (?,?,?,?,?,?,?,?,?,?)", [
                e["proxy_wallet"], e["direction"], e.get("hop", 1), e.get("parent"), e.get("address", e["proxy_wallet"]),
                e.get("status", "ok"), e.get("n_logs", 1), e.get("to_block_ts", RES + 20_000_000), e.get("gap_free", True),
                e.get("has_child_edges", True)])
        con.execute("CREATE TABLE stop_list(address TEXT)")
        for a in self.stop:
            con.execute("INSERT INTO stop_list VALUES (?)", [a])
        con.execute("CREATE TABLE bypass(proxy_wallet TEXT)")
        for b in self.bypass:
            con.execute("INSERT INTO bypass VALUES (?)", [b])
        return con


def run(world, P=None, **kw):
    from radar import signals
    con = world.build()
    signals.compute_all(con, dict(P_DEFAULT, **(P or {})), **kw)
    return con


def sig(con, wallet, signal, scope="all"):
    return con.execute("SELECT raw_value, component, na_reason FROM signals_dense WHERE scope=? AND proxy_wallet=? AND signal_id=?",
                       [scope, wallet, signal]).fetchone()
