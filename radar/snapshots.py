import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from radar.config import SNAPSHOT_DIR


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SnapshotRun:
    """One ingest run = one SNAP-NNN directory. Raw response bytes are written byte-exact;
    snapshot_id = sha256 of the exact bytes object that is written (no re-read)."""

    def __init__(self, root: Path = SNAPSHOT_DIR, snap: str | None = None):
        root.mkdir(parents=True, exist_ok=True)
        if snap is None:
            nums = [int(m.group(1)) for p in root.iterdir() if (m := re.fullmatch(r"SNAP-(\d{3})", p.name))]
            snap = f"SNAP-{(max(nums) + 1 if nums else 1):03d}"
        self.snap = snap
        self.dir = root / snap
        self.dir.mkdir(parents=True, exist_ok=False)
        self.records: list[dict] = []
        self.started_at = utcnow()

    def save(self, source: str, name: str, page: int, url: str, params: dict | None,
             status: int, body: bytes) -> dict:
        sha = hashlib.sha256(body).hexdigest()
        rel = Path("raw") / source / name / f"page{page:04d}.json"
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        rec = {"snapshot_id": sha, "snap": self.snap, "source": source, "name": name, "page": page,
               "endpoint": url, "params_json": json.dumps(params or {}, sort_keys=True),
               "fetched_at": utcnow(), "http_status": status, "bytes": len(body),
               "path": rel.as_posix()}
        self.records.append(rec)
        return rec
