"""S8 side-car classification (params v2). Uses a real (owner, proxy) pair measured on chain 2026-09-26 for CREATE2."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("s8", Path(__file__).resolve().parent.parent / "tools" / "s8_shapes.py")
s8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s8)

OWNER, PROXY = "0xeb041b23ebb4ff5b8adca5465805b3da4b904765", "0x5a218c7ad04135830a45c41aaed7294df7809318"
never = lambda tx: (_ for _ in ()).throw(AssertionError("decode must not be called"))


def test_factory_direct_requires_create2_match():
    tx = {"to": s8.FACTORY, "input": "0x34ee9791" + "00" * 8, "from": OWNER}
    assert s8.classify_v2(tx, PROXY, never) == ("factory_direct", None)
    assert s8.classify_v2(tx, "0x" + "22" * 20, never) == ("other", None)          # someone else's wallet


def test_relay_hub_decode_flag_and_passthrough():
    relay = {"to": "0xd216153c06e857cd7f72665e0af1d7d82172f494", "input": "0x405cec67" + "00" * 8, "from": "0x" + "33" * 20}
    assert s8.classify_v2(relay, PROXY, lambda tx: (True, True)) == ("relay_hub", True)
    assert s8.classify_v2(relay, PROXY, lambda tx: (True, False)) == ("relay_hub", False)
    assert s8.classify_v2(relay, PROXY, lambda tx: (False, True)) == ("relay_hub", False)
    safe = {"to": PROXY, "input": "0x6a761202" + "00" * 8, "from": "0x" + "44" * 20}
    assert s8.classify_v2(safe, PROXY, never) == ("safe_exec", None)
