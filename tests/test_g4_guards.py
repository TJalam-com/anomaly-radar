"""r4 R1/R2 as checks: signal code never reads Gamma volume/fields or holders. The grep is proven live by a planted
reference in a temp copy (a grep that can never match proves nothing)."""
import re
from pathlib import Path

SIGNAL_FILES = [Path(__file__).resolve().parent.parent / "radar" / "signals.py"]
FORBIDDEN = re.compile(r"gamma_volume|volumeNum|volume_shares|outcomePrices|holders_snap|\bholders\b|pnl", re.I)  # r9 §2: P&L display-only


def offending_lines(text):
    return [ln for ln in text.splitlines() if FORBIDDEN.search(ln) and not ln.lstrip().startswith("#")
            and "never read" not in ln and "never reads" not in ln]


def test_signals_do_not_reference_gamma_or_holders():
    for f in SIGNAL_FILES:
        bad = offending_lines(f.read_text(encoding="utf-8"))
        assert not bad, f"{f.name}: forbidden reference(s): {bad}"


def test_guard_grep_fires_on_planted_reference(tmp_path):
    src = SIGNAL_FILES[0].read_text(encoding="utf-8")
    planted = src + ("\n    x = con.execute('SELECT gamma_volume_shares FROM markets_r')\n    y = con.execute('SELECT * FROM holders_snap')\n"
                     "    z = con.execute('SELECT pnl_trades FROM pnl_market')\n")
    assert len(offending_lines(planted)) == 3
