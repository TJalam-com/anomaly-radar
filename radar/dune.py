"""Minimal Dune API client (read-only SQL execution). The key is read from app/.env at call time and is never
printed, logged, returned or written anywhere (QA: local test key, rotate before prod).

execute(sql) -> {"execution_id", "state", "rows", "columns", "credits", "raw_meta"}; raw result bytes can be saved by
the caller (they contain query output only, no credentials).
"""
import json
import time
from pathlib import Path

import httpx

from radar import config

API = "https://api.dune.com/api/v1"


class DuneError(RuntimeError):
    pass


def _key() -> str:
    for line in (config.APP_DIR / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("DUNE_API_KEY="):
            k = line.split("=", 1)[1].strip().strip('"').strip("'")
            if k:
                return k
    raise DuneError("DUNE_API_KEY missing in app/.env")


def execute(sql: str, performance: str | None = None, poll_s: float = 3.0, timeout_s: float = 900, transport=None) -> dict:
    headers = {"X-Dune-API-Key": _key(), "Content-Type": "application/json"}
    with httpx.Client(transport=transport, timeout=120, headers=headers) as cli:
        body = {"sql": sql}
        if performance:
            body["performance"] = performance
        r = cli.post(f"{API}/sql/execute", json=body)
        if r.status_code != 200:
            raise DuneError(f"execute HTTP {r.status_code}: {r.text[:300]}")
        eid = r.json()["execution_id"]
        t0 = time.time()
        while True:
            s = cli.get(f"{API}/execution/{eid}/status")
            st = s.json()
            state = st.get("state", "")
            if state == "QUERY_STATE_COMPLETED":
                break
            if "FAILED" in state or "CANCEL" in state or "EXPIRED" in state:
                raise DuneError(f"execution {eid} {state}: {json.dumps(st.get('error', st))[:400]}")
            if time.time() - t0 > timeout_s:
                raise DuneError(f"execution {eid} timed out in state {state}")
            time.sleep(poll_s)
        rows, cols, meta, offset = [], None, None, 0
        while True:
            res = cli.get(f"{API}/execution/{eid}/results", params={"limit": 50000, "offset": offset})
            if res.status_code != 200:
                raise DuneError(f"results HTTP {res.status_code}: {res.text[:300]}")
            j = res.json()
            result = j.get("result", {})
            rows += result.get("rows", [])
            meta = result.get("metadata", meta)
            cols = (meta or {}).get("column_names", cols)
            nxt = j.get("next_offset")
            if nxt is None:
                break
            offset = nxt
    credits = st.get("execution_cost_credits")
    return {"execution_id": eid, "state": state, "rows": rows, "columns": cols, "credits": credits,
            "raw_meta": {k: v for k, v in st.items() if k != "result"}}
