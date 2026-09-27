"""S6 v3 activity lookups (radar/activity.py; params v3 [fetch] activity_*; D1 r16b §2, R16A-6/7, R16C-2). Fake RPC only."""
import pytest

from radar import activity, knee_g
from radar.profile import TRANSFER, _pad

X = "0x" + "aa" * 20
Y = ["0x" + f"{i:040x}" for i in range(1, 400)]


def log(frm, to, block, li=0):
    return {"blockNumber": hex(block), "logIndex": hex(li), "topics": [TRANSFER, _pad(frm), _pad(to)], "blockTimestamp": hex(1000 + block),
            "transactionHash": "0x" + f"{block:064x}", "address": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"}


class FakeRPC:
    def __init__(self, logs, max_results=10_000, fail_range=None, block_ts=None):
        self.logs, self.max_results, self.fail_range, self.calls, self.block_ts = logs, max_results, fail_range, [], block_ts or (lambda n: 10 * n)

    def rpc_call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_getLogs":
            q = params[0]
            lo, hi = int(q["fromBlock"], 16), int(q["toBlock"], 16)
            if self.fail_range and lo <= self.fail_range <= hi and hi - lo < 4:
                return None, {"message": "internal error"}
            t = q["topics"]
            res = [lg for lg in self.logs if lo <= int(lg["blockNumber"], 16) <= hi
                   and (t[1] is None or lg["topics"][1] == t[1]) and (len(t) < 3 or t[2] is None or lg["topics"][2] == t[2])]
            if len(res) > self.max_results:
                return None, {"message": "query returned more than 10000 results"}
            return res, None
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(self.block_ts(int(params[0], 16)))}, None
        if method == "eth_blockNumber":
            return hex(1000), None
        raise AssertionError(method)


def test_global_merge_order_inbound_earlier_cap_in_outbound_stream():
    logs = [log(Y[0], X, 5), log(X, Y[2], 6), log(Y[1], X, 7), log(X, Y[3], 8)]      # in@5, out@6, in@7, out@8
    rec = activity.lookup_activity(FakeRPC(logs), X, 100, cap=3)
    assert rec["capped"] and rec["cap_block"] == 7                                    # 3rd distinct Y in (block, log_index) order
    assert rec["first_blocks"] == {Y[0]: 5, Y[2]: 6, Y[1]: 7}
    assert knee_g.activity_at(rec, 6) == 2 and knee_g.activity_at(rec, 7) is None      # as-of includes the earlier inbound Y


def test_bisection_gives_the_same_record_and_stays_gap_free():
    logs = [log(Y[i], X, 3 * i + 1) for i in range(20)] + [log(X, Y[30 + i], 3 * i + 2) for i in range(20)]
    a = activity.lookup_activity(FakeRPC(logs), X, 200, cap=257)
    b = activity.lookup_activity(FakeRPC(logs, max_results=3), X, 200, cap=257)
    assert a["first_blocks"] == b["first_blocks"] and a["n_distinct"] == b["n_distinct"] == 40
    assert len(b["subranges"]) > 1 and knee_g.lookup_gap_free(b) and knee_g.complete_at(b, 150)


def test_self_transfer_and_duplicate_stream_log_counted_once():
    logs = [log(X, X, 3), log(Y[0], X, 4), log(X, Y[0], 9)]
    rec = activity.lookup_activity(FakeRPC(logs), X, 100, cap=257)
    assert rec["first_blocks"] == {Y[0]: 4} and rec["n_distinct"] == 1


def test_rpc_error_makes_the_record_incomplete():
    rec = activity.lookup_activity(FakeRPC([log(Y[0], X, 50)], max_results=0, fail_range=50), X, 100, cap=257)
    assert rec["status"] == "unavailable" and not knee_g.complete_at(rec, 60)


def test_cap_stops_fetching_later_ranges():
    logs = [log(Y[i], X, i + 1) for i in range(300)]
    cl = FakeRPC(logs, max_results=50)
    rec = activity.lookup_activity(cl, X, 1000, cap=257)
    assert rec["capped"] and rec["cap_block"] == 257
    assert max(int(c[1][0]["fromBlock"], 16) for c in cl.calls) <= 257                  # nothing fetched past the cap leaf
    assert knee_g.complete_at(rec, 999)                                                # capped records are usable at any later t


def test_uncapped_record_must_cover_block_t():
    rec = activity.lookup_activity(FakeRPC([log(Y[0], X, 5)]), X, 100, cap=257)
    assert knee_g.complete_at(rec, 100) and not knee_g.complete_at(rec, 101)


def test_block_before_is_strict():
    cl = FakeRPC([], block_ts=lambda n: 10 * n + 7)                                     # block n has ts 10n + 7
    assert activity.block_before(cl, 57) == 4                                          # ts(5) = 57 is not < 57
    assert activity.block_before(cl, 58) == 5


def test_lookup_set_rule():
    nat = {"0x1", "0x2"}
    edges = [("0x1", "0xA"), ("0x3", "0xA"), ("0x3", "0xB"), ("0x4", "0xB"), ("0x1", "0xC"), ("0x2", "0xS"), ("0x1", "0xS")]
    ls = activity.lookup_set(edges, nat, stop={"0xs"})
    assert ls == {"0xa": (2, 1)}                    # 0xB: bypass-only; 0xC: single sharer; 0xS: stop-listed
