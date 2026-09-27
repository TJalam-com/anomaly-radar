"""S4 loader on the r3a format (QA ruling 2026-09-27 (a) header map, (b) tz_inferred = unknown -> UTC+14; D1 r16e §2)."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from radar import events

DATA = Path(__file__).resolve().parent / "data"
PROJECT_ROOT = next((q for q in Path(__file__).resolve().parents if (q / "PROJECT_BRIEF.md").exists()), None)
R3A = (PROJECT_ROOT / "results" / "G2E_event_times_2026-09-26_r3a.csv") if PROJECT_ROOT else Path("/nonexistent")   # skipped when absent
R3A_SHA = "60d2c1733733bb5741f187723bd2f869b08261056fb94e03b91b41e481fb8229"
HEADER = (DATA / "g2e_r3a_header.csv").read_text(encoding="utf-8").strip()          # the real r3a header line (byte copy of line 1)
COLS = HEADER.split(",")


def row(**v):
    return ",".join(v.get(c, "") for c in COLS)


def write(tmp_path, rows, header=HEADER):
    f = tmp_path / "ev.csv"
    f.write_text(header + "\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
    return f


def test_stored_header_is_the_real_r3a_header():
    if not R3A.exists():
        pytest.skip("results/ not present on this machine")
    raw = R3A.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == R3A_SHA
    assert raw.decode("utf-8").splitlines()[0].strip() == HEADER


def test_r3a_header_loads_through_the_explicit_map(tmp_path):
    f = write(tmp_path, [row(condition_id="0xabc", event_ts_utc="2026-02-28T06:15:00Z", event_ts_precision="minute", event_ts_tz="UTC",
                             event_ts_tz_inferred="false", tier_gap="false", occurrence_ts_precision="minute")])
    (c, anchor, prec, na), = events.load_anchors(f, 24)[0]
    assert (c, anchor, prec, na) == ("0xabc", datetime(2026, 2, 28, 6, 15, tzinfo=timezone.utc), 60, None)


def test_inferred_zone_is_unknown_and_floors_at_utc_plus_14(tmp_path):
    base = dict(condition_id="0xabc", event_ts_utc="2026-02-28T06:15:00Z", event_ts_precision="hour", event_ts_tz="UTC+3:30",
                tier_gap="false", occurrence_ts_precision="minute")
    stated = events.load_anchors(write(tmp_path, [row(**base, event_ts_tz_inferred="false")]), 24)[0][0][1]
    inferred = events.load_anchors(write(tmp_path, [row(**base, event_ts_tz_inferred="true")]), 24)[0][0][1]
    assert stated == datetime(2026, 2, 28, 5, 30, tzinfo=timezone.utc)     # 09:45 +03:30 -> 09:00 +03:30
    assert inferred == datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc)    # 20:15 +14 -> 20:00 +14


@pytest.mark.parametrize("case", ["unknown_column", "missing_inferred", "double_map", "non_boolean"])
def test_fail_closed_cases(tmp_path, case):
    r = row(condition_id="0xabc", event_ts_utc="2026-02-28T06:15:00Z", event_ts_precision="minute", event_ts_tz="UTC",
            event_ts_tz_inferred="false", tier_gap="false", occurrence_ts_precision="minute")
    if case == "unknown_column":
        f = write(tmp_path, [r + ",x"], HEADER + ",surprise")
    elif case == "missing_inferred":
        cols = [c for c in COLS if c != "hedged_report_tz_inferred"]
        f = write(tmp_path, [",".join(dict(zip(COLS, r.split(","))).get(c, "") for c in cols)], ",".join(cols))
    elif case == "double_map":
        f = write(tmp_path, [r + ",UTC"], HEADER + ",event_tz")
    else:
        f = write(tmp_path, [r.replace(",false,", ",maybe,", 1)])
    with pytest.raises(events.EventFileError):
        events.load_anchors(f, 24)


@pytest.mark.parametrize("s,offset_min,unknown", [
    ("UTC", 0, False), ("GMT", 0, False), ("UTC+3", 180, False), ("+03:00", 180, False), ("IRST(UTC+03:30)", 210, False),
    ("IST(UTC+02:00)", 120, False), ("EST(UTC-05:00)", -300, False), ("", 840, True), ("IRST", 840, True), ("UTC+15", 840, True),
    ("Asia/Tehran", 840, True)])
def test_zone_strings_known_only_from_an_explicit_offset(s, offset_min, unknown):
    tz, unk = events._tz(s)
    assert tz.utcoffset(None).total_seconds() == offset_min * 60 and unk is unknown


def test_r3a_stated_irst_zone_is_honoured_and_inferred_is_floored(tmp_path):
    base = dict(condition_id="0xabc", event_ts_utc="2026-02-28T06:15:00Z", event_ts_precision="hour", event_ts_tz="IRST(UTC+03:30)",
                tier_gap="false", occurrence_ts_precision="minute")
    stated = events.load_anchors(write(tmp_path, [row(**base, event_ts_tz_inferred="false")]), 24)[0][0][1]
    inferred = events.load_anchors(write(tmp_path, [row(**base, event_ts_tz_inferred="true")]), 24)[0][0][1]
    assert stated == datetime(2026, 2, 28, 5, 30, tzinfo=timezone.utc) and inferred == datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc)


def test_real_r3a_stated_irst_anchors_are_0430z_and_known():
    if not R3A.exists():
        pytest.skip("results/ not present on this machine")
    import csv
    import io
    raw = R3A.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == R3A_SHA
    anchors = {c: (a, na) for c, a, p, na in events.load_anchors(R3A, 24)[0]}
    rows = [r for r in csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")) if r["event_id"] in ("E-001", "E-002", "E-003", "E-004")]
    assert len(rows) == 35
    for r in rows:
        assert events._tz(r["event_ts_tz"])[1] is False, r["event_ts_tz"]                 # stated zone, not unknown
        a, na = anchors[r["condition_id"].lower()]
        assert a == datetime(2026, 2, 28, 4, 30, tzinfo=timezone.utc) and na is None      # not floored to 04:00Z (UTC+14)


def test_empty_inferred_flag_next_to_a_timestamp_is_refused(tmp_path):
    base = dict(condition_id="0xabc", event_ts_utc="2026-02-28T06:15:00Z", event_ts_precision="minute", event_ts_tz="UTC",
                tier_gap="false", occurrence_ts_precision="minute")
    with pytest.raises(events.EventFileError, match="empty next to a timestamp"):
        events.load_anchors(write(tmp_path, [row(**base)]), 24)                      # event_ts present, event_ts_tz_inferred empty
    rows = events.load_anchors(write(tmp_path, [row(**base, event_ts_tz_inferred="false")]), 24)[0]   # report ts empty: flag may be empty
    assert rows[0][3] is None
