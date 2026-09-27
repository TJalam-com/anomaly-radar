"""S4 anchors from the event-times file (design note r7; S4 input of record: results/G2E_event_times_2026-09-26_r3a.csv, D1 r16e).

anchor(c) = min(start_of_unit(event_ts_utc, event precision, event tz),
                start_of_unit(hedged_report_ts_utc, report precision, report tz))
Floor in the SOURCE tz (UTC+14 if unknown), then UTC. NA 'event_ts_too_coarse' if the anchor's precision unit exceeds
s4_lead_h, or tier_gap is true and occurrence_ts_precision is not minute/hour. Missing columns -> refuse (no guessing).
COLUMNS maps the r7 roles to canonical headers; HEADER_MAP renames the r3a headers to them (QA ruling 2026-09-27 (a)).
A tz_inferred = true zone counts as UNKNOWN -> the UTC+14 floor (QA ruling (b), consistent with replay R4a).
Fail-closed: a header outside KNOWN_HEADERS, a missing required column, or two headers mapping to one role -> refuse.
"""
import csv
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2678400, "year": 31622400}
COLUMNS = {  # role -> canonical header
    "condition_id": "condition_id", "event_ts": "event_ts_utc", "event_precision": "event_ts_precision",
    "event_tz": "event_tz", "report_ts": "hedged_report_ts_utc", "report_precision": "hedged_report_ts_precision",
    "report_tz": "hedged_report_tz", "tier_gap": "tier_gap", "occurrence_precision": "occurrence_ts_precision",
    "event_tz_inferred": "event_ts_tz_inferred", "report_tz_inferred": "hedged_report_tz_inferred",
}
HEADER_MAP = {"event_ts_tz": "event_tz", "hedged_report_precision": "hedged_report_ts_precision"}   # r3a header -> canonical
KNOWN_HEADERS = set(COLUMNS.values()) | set(HEADER_MAP) | {   # the rest of the r3a header (read, never used by S4)
    "question", "event_id_gamma", "class", "market_type", "resolved_outcome", "outcome_determined_by", "event_id", "related_event_id",
    "reason", "occurrence_ts_utc", "first_report_ts_utc", "event_ts_basis", "spread_minutes", "closedTime", "bound_ok", "retrieved_at"}


def _bool(s, col):
    v = (s or "").strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no", ""):
        return False
    raise EventFileError(f"column {col}: {s!r} is not a boolean; S4 refuses")


class EventFileError(RuntimeError):
    pass


def _tz(s):
    """'UTC', 'GMT', 'UTC+3', '+03:00', 'IRST(UTC+03:30)', 'IST(UTC+02:00)' -> timezone; unknown -> UTC+14 (earliest civil tz).
    A zone is known only from an explicit numeric offset (or bare UTC/GMT): zone NAMES are never looked up (no guessing), so a
    name without an offset, or any unparseable string, is unknown. r3a writes stated zones as NAME(UTC+hh:mm) (D1 r16e §2)."""
    raw = (s or "").strip().upper()
    unknown = (timezone(timedelta(hours=14)), True)
    if raw in ("", "UNKNOWN", "NA", "NONE"):
        return unknown
    if raw in ("UTC", "GMT", "Z"):
        return timezone.utc, False
    m = re.fullmatch(r"(?:[A-Z]{2,5}\s*\()?\s*(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?\s*\)?", raw)
    if not m:
        return unknown
    h, mm = int(m.group(2)), int(m.group(3) or 0)
    if h > 14 or mm >= 60:
        return unknown
    sign = -1 if m.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=h, minutes=mm)), False


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
    heads = list(rd.fieldnames or [])
    unknown = [h for h in heads if h not in KNOWN_HEADERS]
    if unknown:
        raise EventFileError(f"event file has unknown columns {unknown}; S4 refuses (fail-closed)")
    canon = {}
    for h in heads:
        c = HEADER_MAP.get(h, h)
        if c in canon:
            raise EventFileError(f"columns {canon[c]!r} and {h!r} both map to {c!r}; S4 refuses")
        canon[c] = h
    missing = [c for c in COLUMNS.values() if c not in canon]
    if missing:
        raise EventFileError(f"event file lacks columns {missing}; S4 refuses (r7: no guessing)")
    out = []
    for r in rd:
        g = lambda role: (r.get(canon[COLUMNS[role]]) or "").strip()
        terms = []
        for ts_k, p_k, tz_k, inf_k in (("event_ts", "event_precision", "event_tz", "event_tz_inferred"),
                                       ("report_ts", "report_precision", "report_tz", "report_tz_inferred")):
            if g(ts_k) and not g(inf_k):          # QA N2: a present timestamp needs an explicit inferred flag (no silent false)
                raise EventFileError(f"row {g('condition_id')}: {canon[COLUMNS[inf_k]]} is empty next to a timestamp; S4 refuses")
            inferred = _bool(g(inf_k), canon[COLUMNS[inf_k]])
            if g(ts_k):
                tz, _ = _tz("UNKNOWN" if inferred else g(tz_k))     # (b): an inferred zone is unknown -> UTC+14 floor
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
