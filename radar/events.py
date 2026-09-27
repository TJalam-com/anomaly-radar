"""S4 anchors from the event-times file (design note r7; QA: Tester 2's results/G2E_event_times_2026-09-26_r3.csv).

anchor(c) = min(start_of_unit(event_ts_utc, event precision, event tz),
                start_of_unit(hedged_report_ts_utc, report precision, report tz))
Floor in the SOURCE tz (UTC+14 if unknown), then UTC. NA 'event_ts_too_coarse' if the anchor's precision unit exceeds
s4_lead_h, or tier_gap is true and occurrence_ts_precision is not minute/hour. Missing columns -> refuse (no guessing).
COLUMNS maps the r7 roles to file headers; it is checked against the delivered file before use.
"""
import csv
import hashlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2678400, "year": 31622400}
COLUMNS = {  # role -> header (to confirm against the delivered r3 file)
    "condition_id": "condition_id", "event_ts": "event_ts_utc", "event_precision": "event_ts_precision",
    "event_tz": "event_tz", "report_ts": "hedged_report_ts_utc", "report_precision": "hedged_report_ts_precision",
    "report_tz": "hedged_report_tz", "tier_gap": "tier_gap", "occurrence_precision": "occurrence_ts_precision",
}


class EventFileError(RuntimeError):
    pass


def _tz(s):
    """'UTC', 'UTC+3', '+03:00', '' -> timezone. Unknown -> UTC+14 (earliest civil tz)."""
    raw = (s or "").strip().upper()
    if raw in ("", "UNKNOWN", "NA", "NONE"):
        return timezone(timedelta(hours=14)), True
    s = raw.replace("UTC", "").replace("GMT", "")
    if not s:
        return timezone.utc, False
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    h, _, m = s.partition(":")
    try:
        return timezone(sign * timedelta(hours=int(h), minutes=int(m or 0))), False
    except ValueError:
        return timezone(timedelta(hours=14)), True


def start_of_unit(ts_utc: datetime, precision: str, tz):
    local = ts_utc.astimezone(tz)
    p = precision.strip().lower()
    if p == "second":
        f = local.replace(microsecond=0)
    elif p == "minute":
        f = local.replace(second=0, microsecond=0)
    elif p == "hour":
        f = local.replace(minute=0, second=0, microsecond=0)
    elif p == "day":
        f = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif p == "month":
        f = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif p == "year":
        f = local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise EventFileError(f"unknown precision {precision!r}")
    return f.astimezone(timezone.utc)


def _parse_ts(s):
    s = s.strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load_anchors(path: Path, lead_h: float):
    raw = Path(path).read_bytes()
    rd = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    missing = [h for h in COLUMNS.values() if h not in (rd.fieldnames or [])]
    if missing:
        raise EventFileError(f"event file lacks columns {missing}; S4 refuses (r7: no guessing)")
    out = []
    for r in rd:
        g = lambda role: (r.get(COLUMNS[role]) or "").strip()
        terms = []
        for ts_k, p_k, tz_k in (("event_ts", "event_precision", "event_tz"), ("report_ts", "report_precision", "report_tz")):
            if g(ts_k):
                tz, _ = _tz(g(tz_k))
                terms.append((start_of_unit(_parse_ts(g(ts_k)), g(p_k) or "day", tz), UNIT_S[(g(p_k) or "day").lower()]))
        c = g("condition_id").lower()
        if not terms:
            out.append((c, None, None, "no_event_ts")); continue
        terms.sort(key=lambda t: (t[0], t[1]))
        anchor, prec = terms[0]
        na = None
        if prec >= lead_h * 3600:  # r7 example (day vs 24 h lead -> NA) requires >=; flagged to QA
            na = "event_ts_too_coarse"
        elif g("tier_gap").lower() in ("true", "1", "yes") and g("occurrence_precision").lower() not in ("minute", "hour"):
            na = "event_ts_too_coarse"
        out.append((c, anchor, prec, na))
    return out, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(out)}
