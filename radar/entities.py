"""S6 stop-list (design notes r4 §3 S6, r5 Δ1 F2, r6 Δ6): addresses that must never link two wallets.

Two sources, kept distinguishable:
  POLYMARKET_INFRA  measured / Polygonscan-labelled on 2026-09-26 (verified_on_polygon = True where a label was read)
  pselamy seed      vendored MIT list (radar/vendor/pselamy_entity_data.py), verified_on_polygon = False
Kinds: cex | bridge | dex | token | defi | polymarket_infra | wrapper.
The high-fan-out rule (P.s6_fanout_max) removes further shared hubs at signal time; it is not encoded here.
"""
from radar.vendor import pselamy_entity_data as _ps

POLYMARKET_INFRA = {
    # address: (label, kind, source, verified_on_polygon)
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e": ("Polymarket: CTF Exchange", "polymarket_infra", "polygonscan label 2026-09-26", True),
    "0xc5d563a36ae78145c45a50134d48a1215220f80a": ("Polymarket: Neg Risk CTF Exchange", "polymarket_infra", "polygonscan label 2026-09-26", True),
    "0xe111180000d2663c0091e4f400237545b87b996b": ("Polymarket: CTF Exchange V2", "polymarket_infra", "polygonscan label 2026-09-26", True),
    "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296": ("Polymarket: Neg Risk Adapter", "polymarket_infra", "polygonscan label 2026-09-26", True),
    "0xd216153c06e857cd7f72665e0af1d7d82172f494": ("Polymarket: Relay Hub", "polymarket_infra", "polygonscan label 2026-09-26", True),
    "0x3a3bd7bb9528e159577f7c2e685cc81a765002e2": ("Polymarket: Wrapped Collateral", "wrapper", "polygonscan label 2026-09-26", True),
    "0x4d97dcd97ec945f40cf65f87097ace5ea0476045": ("CTF ConditionalTokens (ERC-1155)", "polymarket_infra", "measured: ERC-1155 emitter in fills (census)", False),
    "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb": ("pUSD Polymarket USD (collateral token)", "token", "measured: name()/symbol() eth_call (census)", True),
    "0x0000000000000000000000000000000000000000": ("zero address (mint/burn)", "token", "definition", True),
}

_KIND = {"cex": "cex", "bridge": "bridge", "dex": "dex", "token": "token", "defi": "defi"}


def rows():
    """[(address, label, kind, source, verified_on_polygon)] — the stop-list, POLYMARKET_INFRA first."""
    out = [(a, *v) for a, v in POLYMARKET_INFRA.items()]
    seen = set(POLYMARKET_INFRA)
    for addr, et in _ps.get_all_known_entities().items():
        if addr in seen:
            continue
        kind = _KIND.get(et.value.split("_")[0], "other")
        out.append((addr, et.value, kind, "vendored pselamy entity_data @302c6b99 (Etherscan/Arkham labels)", False))
        seen.add(addr)
    return out
