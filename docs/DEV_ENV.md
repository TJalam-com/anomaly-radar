# Dev environment (principal D-007: nothing on C:)

Use `./uvd.sh` (bash) or `./uvd.ps1` (PowerShell) instead of `uv` inside `app/`.

uv has no config-file key for the Python install dir or the shim dir. The wrapper therefore sets these environment variables on each call:

| env var | D: path | default on C: that it replaces |
|---|---|---|
| `UV_PYTHON_INSTALL_DIR` | `scratch/developer/uv-python` | `%APPDATA%\uv\python` |
| `UV_PYTHON_BIN_DIR` | `scratch/developer/uv-python-bin` | `%USERPROFILE%\.local\bin` |
| `UV_CACHE_DIR` | `scratch/developer/uv-cache` (also in `pyproject.toml [tool.uv] cache-dir`) | `%LOCALAPPDATA%\uv\cache` |
| `UV_TOOL_DIR` | `scratch/developer/uv-tools` | `%APPDATA%\uv\tools` |

Other temp paths:
- `.venv` home = `scratch/developer/uv-python/cpython-3.11-windows-x86_64-none` (see `.venv/pyvenv.cfg`)
- DuckDB spill = `app/data/tmp`, memory_limit 2GB (`radar/config.py`)
- pytest basetemp = `scratch/developer/pytest-tmp` (`pyproject.toml`)

A plain `uv python install` run outside the wrapper still installs to C:. That happened once on 2026-09-26; see the QA report of that date.
