"""Profile writer identity (design notes r14 Δ18, r15 Δ26/Δ27; QA conditions C1–C3 on r15).

writer_id = sha256 of the canonical JSON of the sorted code closure: every radar module loaded in the writer process
(module path relative to the app dir, file sha256), the interpreter version, the uv.lock sha, the installed versions
of duckdb / pyarrow / httpx, and the params file bytes. The closure is recorded in every profile file's manifest entry
context; the pin lives only in the QA-held ledger/WRITER_PINS.md (no allow-list: C2).

WRITER_PINS.md line format (C1, verbatim):  PIN | <PROF id> | <writer_id 64 hex> | <instant> | QA
Only lines starting "PIN |" are parsed; a PROF id may have several PIN lines.
"""
import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path

PIN_RE = re.compile(r"^PIN \| (?P<prof>[A-Za-z0-9._-]+) \| (?P<wid>[0-9a-f]{64}) \| (?P<instant>[^|]+?) \| QA\s*$")
LIBS = ("duckdb", "pyarrow", "httpx")


class WriterError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def closure(app_dir: Path, params_path: Path) -> list:
    """Sorted list of [kind, name, value] describing everything that decides the writer's output."""
    app_dir = Path(app_dir).resolve()
    items = []
    for name, mod in sorted(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if (name == "radar" or name.startswith("radar.")) and f:
            p = Path(f).resolve()
            items.append(["module", p.relative_to(app_dir).as_posix(), _sha(p)])
    items.append(["python", "version", sys.version])
    lock = app_dir / "uv.lock"
    items.append(["lockfile", "uv.lock", _sha(lock) if lock.exists() else "missing"])
    for lib in LIBS:
        try:
            items.append(["library", lib, importlib.metadata.version(lib)])
        except importlib.metadata.PackageNotFoundError:
            items.append(["library", lib, "missing"])
    items.append(["params", Path(params_path).name, _sha(Path(params_path))])
    return sorted(items)


def writer_id(items: list) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assert_unchanged(items: list, app_dir: Path, params_path: Path) -> None:
    """Re-hash the closure before a write; any drift (a module edited mid-run) aborts the writer."""
    now = closure(app_dir, params_path)
    if now != items:
        drift = [x for x in now if x not in items] + [x for x in items if x not in now]
        raise WriterError(f"writer closure changed during the run: {drift[:4]}")


def read_pins(pins_path: Path) -> list:
    """All PIN lines of the QA ledger file: [(prof_id, writer_id, instant)]. Missing file -> []."""
    p = Path(pins_path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("PIN |"):
            m = PIN_RE.match(line.strip())
            if m:
                out.append((m["prof"], m["wid"], m["instant"].strip()))
    return out


def pinned_ids(pins_path: Path, prof_id: str) -> set:
    return {wid for prof, wid, _ in read_pins(pins_path) if prof == prof_id}


def require_pinned(pins_path: Path, prof_id: str, wid: str) -> str:
    """Writer start gate: refuse unless this writer_id is pinned for prof_id. Returns the pins file sha."""
    p = Path(pins_path)
    if not p.exists():
        raise WriterError(f"WRITER_PINS file not found: {p} (QA-held ledger; writer refuses without it)")
    if wid not in pinned_ids(p, prof_id):
        raise WriterError(f"writer_id {wid} is not pinned for {prof_id} in {p.name}; send it to QA for a PIN line")
    return _sha(p)


def prof001_shas(profiles_dir: Path) -> set:
    """r15 Δ29: union of files[].sha256 over EVERY manifest*.json under PROF-001 (frozen, preserved and prefreeze)."""
    out = set()
    for m in sorted((Path(profiles_dir) / "PROF-001").glob("manifest*.json")):
        out |= {f["sha256"] for f in json.loads(m.read_bytes()).get("files", [])}
    return out


def check_profile(prof_dir: Path, pins_path: Path, profiles_dir: Path | None = None) -> dict:
    """Reader gate (r15 Δ26/Δ29, QA C2/C3). Refuse unless: the manifest names its PROF id; EVERY files[] entry carries a
    writer_id pinned for that PROF id in WRITER_PINS.md (no allow-list); no files[] sha belongs to PROF-001; every listed
    file (and every derived file) on disk has the listed sha. Returns a summary for run.json."""
    prof_dir = Path(prof_dir)
    mpath = prof_dir / "manifest.json"
    if not mpath.exists():
        raise WriterError(f"profile {prof_dir} has no manifest.json")
    man = json.loads(mpath.read_bytes())
    prof_id = man.get("prof_id")
    if not prof_id:
        raise WriterError(f"profile {prof_dir.name}: manifest has no prof_id (writer unverified)")
    pins = pinned_ids(pins_path, prof_id)
    if not pins:
        raise WriterError(f"profile {prof_id}: no PIN line in {Path(pins_path).name}")
    files = man.get("files", [])
    if not files:
        raise WriterError(f"profile {prof_id}: manifest lists no files")
    bad = [f["path"] for f in files if f.get("writer_id") not in pins]
    if bad:
        raise WriterError(f"profile {prof_id}: {len(bad)} files[] entries have a missing or unpinned writer_id, e.g. {bad[:3]}")
    if profiles_dir is not None:
        old = prof001_shas(profiles_dir) & {f["sha256"] for f in files}
        if old:
            raise WriterError(f"profile {prof_id}: {len(old)} files come from PROF-001 (full re-trace required)")
    for f in files + [{"path": d["file"], "sha256": d["sha256"]} for d in man.get("derived", [])]:
        p = prof_dir / f["path"]
        if not p.exists() or _sha(p) != f["sha256"]:
            raise WriterError(f"profile {prof_id}: {f['path']} missing or sha mismatch")
    return {"prof_id": prof_id, "manifest_sha256": _sha(mpath), "writer_ids": sorted({f["writer_id"] for f in files}),
            "pins_sha256": _sha(Path(pins_path)), "n_files": len(files)}
