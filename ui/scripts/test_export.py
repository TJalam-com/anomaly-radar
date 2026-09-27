"""G6 C0-9 / U7 (O5): the UI data layer can never select total_scorer / profiled / bypass columns.
Planted controls: a query selecting a forbidden column must raise; SELECT * must raise; the exported JSON has no
forbidden key. Run: python -m pytest -q scripts/test_export.py (app venv)."""
import json
import re
from pathlib import Path

import pytest

import importlib.util
spec = importlib.util.spec_from_file_location("exp", Path(__file__).with_name("export_ui_data.py"))
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOTS = [ROOT / d for d in ("data", "data-dev", "data-review") if (ROOT / d).exists()]


def real_run_files():
    """exported JSON of REAL runs only; fixture runs (context.fixture = true) carry planted cases on purpose."""
    out = []
    for root in DATA_ROOTS:
        for ctx in root.glob("*/context.json"):
            if not json.loads(ctx.read_bytes()).get("fixture"):
                out += list(ctx.parent.glob("*.json"))
    return out


@pytest.mark.parametrize("sql", [
    "SELECT proxy_wallet, total_scorer FROM scores",
    "SELECT proxy_wallet, profiled FROM universe",
    "SELECT u.bypass_listed FROM universe u",
    "SELECT * FROM scores",
    "SELECT * REPLACE (x AS y) FROM t",
])
def test_guard_refuses_forbidden_or_star(sql):
    with pytest.raises(exp.ExportError):
        exp.guard(sql)


def test_guard_allows_prefilter_and_pipeline():
    assert exp.guard("SELECT s.proxy_wallet, s.total_pipeline, u.passed_prefilter FROM s JOIN u USING (scope)")


def test_every_export_query_passes_the_guard():
    """Every SQL string literal in the exporter that starts with SELECT goes through guard() at run time; this also
    checks the source text so a query added outside q() is caught."""
    src = Path(exp.__file__).read_text(encoding="utf-8")
    assert "con.execute(guard(sql)" in src
    for m in re.finditer(r'f?"""\s*(SELECT[\s\S]*?)"""', src):
        exp.guard(m.group(1))


FRESH_CLONE = "no exported run data in this checkout; run export_ui_data.py first"


def real_files_or_skip():
    """QA (public repo): a fresh clone has NO data root (ui/data is not published) -> skip. A data root that exists but holds no
    real-run JSON -> fail (the vacuity guard stays intact locally)."""
    if not DATA_ROOTS:
        pytest.skip(FRESH_CLONE)
    files = real_run_files()
    assert files, "a data root exists but holds no exported real-run data (vacuity guard)"
    return files


def test_data_guard_skips_on_a_fresh_clone_and_fails_on_an_empty_root(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "DATA_ROOTS", [])
    with pytest.raises(pytest.skip.Exception, match="no exported run data"):
        real_files_or_skip()
    monkeypatch.setitem(globals(), "DATA_ROOTS", [tmp_path])                  # a data root with no real run in it
    with pytest.raises(AssertionError, match="vacuity guard"):
        real_files_or_skip()


def test_exported_json_has_no_forbidden_keys():
    files = real_files_or_skip()
    for f in files:
        raw = f.read_bytes().lower()
        assert b"total_scorer" not in raw and b"bypass" not in raw, f
        assert not re.search(rb'"profiled"\s*:', raw), f
        assert b"insider" not in raw, f
        obj = json.loads(f.read_bytes())
        if isinstance(obj, dict) and "rows" in obj:
            assert all("profiled" not in r and "total_scorer" not in r for r in obj["rows"])


def _strict(body):
    def bad(tok):
        raise ValueError(f"non-standard JSON constant {tok}")
    return json.loads(body, parse_constant=bad)


def test_dumps_maps_nonfinite_to_null_and_is_strict_json():
    obj = {"S1": {"raw": float("inf"), "component": 0.0}, "l": [float("-inf"), float("nan"), 1.5], "n": 3}
    got = _strict(exp.dumps(obj))
    assert got == {"S1": {"raw": None, "component": 0.0}, "l": [None, None, 1.5], "n": 3}
    with pytest.raises(ValueError):                           # planted control: the strict parser does reject Infinity
        _strict(b'{"raw":Infinity}')


def test_exported_real_runs_are_strict_json():
    files = [f for f in real_files_or_skip() if f.name in ("leaderboard_all.json", "context.json", "markets.json")]
    for f in files:
        _strict(f.read_bytes())


def test_fixture_runs_are_marked_and_separate():
    """a fixture run is never mixed into real data: context.fixture is true and its run id is not a real run id."""
    if not DATA_ROOTS:
        pytest.skip(FRESH_CLONE)
    for root in DATA_ROOTS:
        for ctx in root.glob("*/context.json"):
            c = json.loads(ctx.read_bytes())
            if c.get("fixture"):
                assert c["run_id"].startswith("fixture-") and ctx.parent.name.startswith("fixture-")
                assert "FIXTURE" in c["banner"]


# ---- V4 ego view: export_links rebuilds the S6 relation and must reproduce S6 raw (two mechanisms, one number)
def _links_world(tmp_path, transfers, raw, fan=20):
    import duckdb
    pdir = tmp_path / "PROF-X"; (pdir / "derived").mkdir(parents=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t(proxy_wallet TEXT, direction TEXT, hop INT, counterparty TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", transfers)
    con.execute(f"COPY t TO '{(pdir / 'derived' / 'transfers.parquet').as_posix()}' (FORMAT parquet)")
    (pdir / "manifest.json").write_bytes(b'{"prof": "x"}')
    rj = {"profile": {"path": str(pdir), "manifest_sha256": exp.sha(pdir / "manifest.json")},
          "params_json": {"s6_max_hops": 3, "s6_fanout_max": fan}}
    sig = {w: {"S6": {"raw": r, "na_reason": None, "evidence": {}}} for w, r in raw.items()}
    return duckdb.connect(), rj, sig


A, B, C, D = ("0x" + c * 40 for c in "abcd")
STOP = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"      # Polymarket CTF Exchange (stop list)
T = [(A, "in", 1, "0x" + "1" * 40), (B, "in", 1, "0x" + "1" * 40), (B, "out", 2, "0x" + "2" * 40), (C, "out", 2, "0x" + "2" * 40),
     (A, "in", 1, STOP), (D, "in", 1, STOP)]           # A-B via hop-1 funder, B-C via hop-2 destination; A-D only via stop-listed


def test_export_links_parity_stop_list_and_files(tmp_path):
    con, rj, sig = _links_world(tmp_path, T, {A: 1.0, B: 2.0, C: 1.0, D: 0.0})
    files, meta = exp.export_links(con, rj, tmp_path / "out", sig, {A: 0.5, B: 0.4, C: 0.3, D: 0.1})
    assert set(files) == {A, B, C, D} and meta["n_pairs"] == 2
    b = json.loads((tmp_path / "out" / "links" / f"{B}.json").read_bytes())
    assert [x[0] for x in b["linked"]] == [A, C] and b["linked"][0][2] == 0.5
    assert sorted(c for c, _, _ in b["cps"]) == ["0x" + "1" * 40, "0x" + "2" * 40]
    body = b"".join(p.read_bytes() for p in (tmp_path / "out" / "links").glob("*.json"))
    assert STOP.encode() not in body                                       # the stop-listed counterparty never appears
    d = json.loads((tmp_path / "out" / "links" / f"{D}.json").read_bytes())
    assert d["n_linked"] == 0


def test_export_links_refuses_when_s6_raw_disagrees(tmp_path):
    con, rj, sig = _links_world(tmp_path, T, {A: 1.0, B: 3.0, C: 1.0, D: 0.0})   # planted: B raw 3, rebuilt 2
    with pytest.raises(exp.ExportError, match="disagrees with S6 raw"):
        exp.export_links(con, rj, tmp_path / "out", sig, {})


def test_export_links_fanout_excludes_shared_hub(tmp_path):
    con, rj, sig = _links_world(tmp_path, T, {A: 0.0, B: 1.0, C: 1.0, D: 0.0}, fan=1)  # fan 1: every shared cp is a hub
    with pytest.raises(exp.ExportError):                                   # raw says linked, rule says not -> refuse
        exp.export_links(con, rj, tmp_path / "out", sig, {})
    con, rj, sig = _links_world(tmp_path / "b", T, {A: 0.0, B: 0.0, C: 0.0, D: 0.0}, fan=1)
    files, meta = exp.export_links(con, rj, tmp_path / "b" / "out", sig, {})
    assert meta["n_pairs"] == 0


def test_export_links_refuses_tampered_profile_manifest(tmp_path):
    con, rj, sig = _links_world(tmp_path, T, {A: 1.0, B: 2.0, C: 1.0, D: 0.0})
    (tmp_path / "PROF-X" / "manifest.json").write_bytes(b'{"prof": "tampered"}')
    with pytest.raises(exp.ExportError, match="manifest hash"):
        exp.export_links(con, rj, tmp_path / "out", sig, {})
