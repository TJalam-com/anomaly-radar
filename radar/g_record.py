"""S6 v3 G record gate (params v3 s6_activity_max = "derived"; D1 r16b §3 procedure step 6): the scorer reads G ONLY from a G record
that QA recorded. Ledger line (ledger/G_RECORDS.md, QA-held, same pattern as WRITER_PINS.md):
    GREC | <PROF id> | <sha256 of the G record file, 64 hex> | G=<int> | <instant> | QA
The record (written by tools/derive_g.py before any scoring) binds G to the profile's lookup tables and the params file."""
import hashlib
import json
import re
from pathlib import Path

GREC_RE = re.compile(r"^GREC \| ([A-Za-z0-9._-]+) \| ([0-9a-f]{64}) \| G=(\d+) \| ([^|]+?) \| QA\s*$")   # profile id as in WRITER_PINS.md


class GRecordError(RuntimeError):
    pass


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def recorded(ledger: Path) -> dict:
    """{record sha: {prof_id, G, instant, superseded}}. R15V3-6: the same sha on two lines with different fields -> refuse (no
    last-wins). R15V3-7: per profile id only the LAST GREC line is valid; records named by earlier lines are superseded."""
    if not Path(ledger).exists():
        return {}
    out, last = {}, {}
    for line in Path(ledger).read_text(encoding="utf-8").splitlines():
        m = GREC_RE.match(line.strip())
        if not m:
            continue
        e = {"prof_id": m.group(1), "G": int(m.group(3)), "instant": m.group(4)}
        prev = out.get(m.group(2))
        if prev is not None and {k: prev[k] for k in e} != e:
            raise GRecordError(f"ledger names record {m.group(2)[:12]}… twice with different fields: refusing")
        out[m.group(2)] = e
        last[e["prof_id"]] = m.group(2)
    for sha_, e in out.items():
        e["superseded"] = last[e["prof_id"]] != sha_
    return out


def check(record_path: Path, ledger: Path, prof: Path, params_sha: str, snap_manifest_sha: str) -> dict:
    """Return {'G', 'block_t', 'record_sha256', ...} or raise GRecordError. Refuses: missing file, unrecorded sha, G or profile id
    differing from the ledger, lookup tables differing from the ones G was derived on, a different snap block or params file."""
    if record_path is None or not Path(record_path).exists():
        raise GRecordError("S6 v3 needs a QA-recorded G record (--g-record); none given")
    rsha = sha(record_path)
    led = recorded(ledger).get(rsha)
    if led is None:
        raise GRecordError(f"G record {rsha[:12]}… is not recorded in {Path(ledger).name}")
    if led["superseded"]:
        raise GRecordError(f"G record {rsha[:12]}… is superseded by a later GREC line for {led['prof_id']}")
    rec = json.loads(Path(record_path).read_bytes())
    man = json.loads((Path(prof) / "manifest.json").read_bytes())
    if rec.get("G") != led["G"] or rec.get("prof_id") != led["prof_id"] or man.get("prof_id") != led["prof_id"]:
        raise GRecordError("G record / ledger / profile disagree on G or the profile id")
    derived = {d["file"]: d["sha256"] for d in man.get("derived", [])}
    for f in ("derived/lookups.parquet", "derived/lookup_y.parquet", "derived/transfers.parquet"):
        if rec.get("inputs", {}).get(f) != derived.get(f) or derived.get(f) is None or sha(Path(prof) / f) != derived[f]:
            raise GRecordError(f"G record input {f} differs from the profile's")
    if rec.get("snap_block") != man.get("snap_block"):
        raise GRecordError("G record snap_block differs from the profile's")
    if rec.get("params_sha256") != params_sha:
        raise GRecordError("G record was derived under a different params file")
    snap_rec = rec.get("snap_manifest_sha256")
    if not snap_rec or snap_rec != man.get("snap_manifest_sha256") or snap_rec != snap_manifest_sha:
        raise GRecordError("G record SNAP differs from the profile's SNAP or the scorer's SNAP")
    return {"G": int(rec["G"]), "block_t": int(man["snap_block"]), "record_sha256": rsha, "prof_id": led["prof_id"],
            "recorded_at": led["instant"], "mode_bin": rec.get("mode_bin"), "population_n": rec.get("population_n")}
