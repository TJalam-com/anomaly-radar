"""S6 v3 activity threshold G and the as-of activity rule (params v3 s6_g_*, s6_capped_at_t; D1 notes r16b §2-3, r16c, r16d).

activity_at is the ONE as-of rule: the G population and the scorer both call it, and it needs the record's first_blocks, so no
caller can substitute a precomputed value. Capped records at t are None (activity > 256: terminal bin / > G).
bins: k = floor(log2(max(1, a))) for k = 0..7 (a = 0..255); terminal bin 8 = every a >= 256, capped-at-t included.
mode = bin in 0..7 with the largest count (ties: lowest k); from mode+1 upward the first k (<= 8) with count(k) < 0.5 * count(k-1)
gives G = 2^k - 1; none -> G = 256; clamp to [16, 256]; bins 0..7 all empty -> G undefined (raise: stop and report, no fallback)."""
import math

DROP, GMIN, GMAX, TERMINAL = 0.5, 16, 256, 8


class GUndefined(ValueError):
    pass


def capped_at(record, block_t):
    """R16C-2: capped at t only if C was reached at or before block(t); a cap reached later says nothing about t."""
    return bool(record["capped"]) and record["cap_block"] is not None and record["cap_block"] <= int(block_t)


def activity_at(record, block_t):
    """As-of activity at block(t) (= last block with ts < t): None if capped at t, else #distinct Y with first_block <= block(t).
    Requires record['first_blocks'] (dict or list of first-seen blocks); a precomputed activity is refused."""
    if "first_blocks" not in record:
        raise ValueError("activity_at needs first_blocks: the as-of rule cannot be bypassed with a precomputed value")
    if capped_at(record, block_t):
        return None
    fb = record["first_blocks"]
    fb = fb.values() if isinstance(fb, dict) else fb
    return sum(1 for b in fb if b <= int(block_t))


def lookup_gap_free(rec) -> bool:
    """every leaf ok, contiguous from from_block; ends at to_block, or at the leaf where the cap was reached."""
    if rec.get("status") != "ok" or not isinstance(rec.get("to_block"), int):
        return False
    subs = sorted(rec.get("subranges") or [], key=lambda x: x["from"])
    if not subs or subs[0]["from"] > rec["from_block"] or not all(x["status"] == "ok" for x in subs):
        return False
    if not all(b["from"] == a["to"] + 1 for a, b in zip(subs, subs[1:])):
        return False
    return bool(subs[-1].get("capped_here")) if rec.get("capped") else subs[-1]["to"] == rec["to_block"]


def complete_at(rec, block_t) -> bool:
    """usable at block(t): gap-free, and capped (exact below cap_block, > C-1 at/after it) or read up to at least block(t)."""
    if rec is None or not lookup_gap_free(rec):
        return False
    return bool(rec["capped"]) or rec["to_block"] >= int(block_t)


def histogram(activities):
    h = [0] * (TERMINAL + 1)
    for a in activities:
        if a is None or a >= 256:
            h[TERMINAL] += 1
        else:
            h[int(math.floor(math.log2(max(1, int(a)))))] += 1
    return h


def g_from_histogram(h):
    if len(h) != TERMINAL + 1 or sum(h[:TERMINAL]) == 0:
        raise GUndefined("no uncapped activity in bins 0..7: G undefined (stop, report to QA, no fallback)")
    top = max(h[:TERMINAL])
    mode = h[:TERMINAL].index(top)                       # first index = lowest k on ties
    g = GMAX
    for k in range(mode + 1, TERMINAL + 1):
        if h[k] < DROP * h[k - 1]:
            g = 2 ** k - 1
            break
    return max(GMIN, min(GMAX, g)), mode


def g_population(records, block_t):
    """s6_g_population: as-of activity at block(SNAP) of COMPLETE lookup records of counterparties shared by >= 2 NATURAL wallets."""
    return [activity_at(r, block_t) for r in records if r["complete"] and r["n_natural_sharers"] >= 2]


def g_from_activities(activities):
    h = histogram(activities)
    g, mode = g_from_histogram(h)
    return {"G": g, "mode_bin": mode, "histogram": h}
