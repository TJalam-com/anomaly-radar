# D1 design note r16g: writer_id closure (canonical definition)

Base: r14 Δ18, r15 Δ26/Δ27 (writer identity), QA C1–C3.

Finding (Developer, 2026-09-27): the r15 closure hashed only the `radar.*` modules present in `sys.modules`.
- Under `python -m radar.profile` the writer runs as `__main__`, so radar/profile.py was never hashed.
- radar/activity.py and radar/knee_g.py are imported lazily after the closure is computed, so they were missed too.
- The resulting id (c6601d26…) was never pinned, and no data was written under it.

## Canonical definition (recompute from this text)

1. **Entries**, each a list of three strings `[kind, name, value]`:
   - `["module", <path relative to the app dir, "/" separators>, <sha256 hex, lower case, of the file bytes>]` for EVERY file matching `radar/**/*.py` on disk, whether imported or not. Paths with a `__pycache__` component are excluded. The whole package is covered, including radar/profile.py (the writer) and radar/activity.py / knee_g.py (the lookups).
   - `["python", "version", <sys.version>]`: the full interpreter version string.
   - `["lockfile", "uv.lock", <sha256 hex of app/uv.lock bytes, or "missing">]`.
   - `["library", <name>, <importlib.metadata.version(name), or "missing">]`, for name in duckdb, pyarrow, httpx.
   - `["params", <basename of the params file>, <sha256 hex of its bytes>]`.
2. **Order:** `sorted(entries)`: Python list order, by kind, then name, then value; plain code-point comparison of the strings.
3. **Bytes:** `json.dumps(sorted_entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")`. There is no whitespace between tokens and no trailing newline. Non-ASCII characters are escaped as `\uXXXX`.
4. **Id:** `writer_id = sha256(bytes)` as lower-case hex (64 characters).

The profile writer prints the full closure with `--print-writer-id`: every hashed file with its sha, plus the non-module entries. The manifest records it as `writer_closure`. `assert_unchanged` re-hashes the closure before each write and aborts on any drift. The pin lives only in the QA-held ledger/WRITER_PINS.md.

## Tests and breaks

In tests/test_g4_profile_events.py:
- **Simulated `-m radar.profile`** (no radar.* module in sys.modules): all four package files are listed.
  - An edit to an un-imported activity.py changes the id.
  - An edit under `__pycache__` does not change the id.
  - A mid-run edit of profile.py makes assert_unchanged raise.
- **Canonical definition:** independent code builds the entries, sorts them and serializes them compactly as ASCII JSON. Its sha256 must equal writer_id(closure()).
- **break_g4 mutants (3/3 each):**
  - closure over profile.py only;
  - closure = loaded sys.modules only (the pre-r16g rule);
  - `__pycache__` files hashed;
  - JSON separators with spaces.
