"""Determinism gate (QA ruling 2026-09-27). A score run counts only if it passes BOTH checks; otherwise it is VOID.

  (i)  run A and run B, each threads=1 in its own process, same inputs: universe/signals/scores parquet byte-identical
  (ii) run C, threads=4, same inputs: the same three files row-identical to A
       (sorted per-column sha256 over every column + EXCEPT ALL = 0 in both directions)

Separate processes, so process-level nondeterminism (hash seeds, unseeded RNGs, wall clock) reaches (i).
threads=4 changes aggregation order, so order-dependent numerics (float sums, unordered ties) reach (ii).

python -m radar.gate <radar.score arguments, without --threads/--runs-dir> [--runs-dir D]
A lands in D (default data/runs); B and C in D/_gate/<A run_id>/{B,C}; the verdict is written to <A>/gate.json.
On failure A's directory is renamed <run_id>+VOID and the exit code is 1.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import duckdb

from radar import config, score
from radar.snapshots import utcnow

GATED = ("universe", "signals", "scores")
C_THREADS = 4


class GateError(RuntimeError):
    pass


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _score(args: list[str], threads: int, runs_dir: Path) -> Path:
    root = Path(score.__file__).resolve().parent.parent      # run the SAME radar package (app or a mirror) in a fresh process
    r = subprocess.run([sys.executable, "-B", "-m", "radar.score", *args, "--threads", str(threads), "--runs-dir", str(runs_dir)],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise GateError(f"score run (threads={threads}) failed rc={r.returncode}: {r.stderr[-800:]}")
    out = json.loads(r.stdout[r.stdout.index("{"):])
    return Path(out["dir"])


def row_equal(a: Path, b: Path) -> dict:
    con = duckdb.connect()
    con.execute("SET threads=1"); con.execute("SET memory_limit='1500MB'")
    con.execute(f"SET temp_directory='{config.TMP_DIR.as_posix()}'")
    ca = con.execute(f"DESCRIBE SELECT * FROM '{a.as_posix()}'").fetchall()
    cb = con.execute(f"DESCRIBE SELECT * FROM '{b.as_posix()}'").fetchall()
    res = {"schema_equal": ca == cb}
    if ca != cb:
        return {**res, "equal": False}
    cols = [c[0] for c in ca]
    order = ", ".join(f'CAST("{c}" AS VARCHAR) NULLS FIRST' for c in cols)
    hashes = {}
    for tag, p in (("a", a), ("b", b)):
        con.execute(f"CREATE OR REPLACE TEMP TABLE _s AS SELECT *, row_number() OVER (ORDER BY {order}) AS _rn FROM '{p.as_posix()}'")
        hashes[tag] = {}
        for c in cols:   # chunked (100k rows): one string per column would need GBs on 7M-row files
            h = hashlib.sha256()
            for (m,) in con.execute(f"""SELECT md5(string_agg(COALESCE(CAST("{c}" AS VARCHAR), '<NULL>'), chr(31) ORDER BY _rn))
                                        FROM _s GROUP BY _rn // 100000 ORDER BY _rn // 100000""").fetchall():
                h.update(m.encode())
            hashes[tag][c] = h.hexdigest()
    sel = ", ".join(f'"{c}"' for c in cols)
    ab = con.execute(f"SELECT count(*) FROM (SELECT {sel} FROM '{a.as_posix()}' EXCEPT ALL SELECT {sel} FROM '{b.as_posix()}')").fetchone()[0]
    ba = con.execute(f"SELECT count(*) FROM (SELECT {sel} FROM '{b.as_posix()}' EXCEPT ALL SELECT {sel} FROM '{a.as_posix()}')").fetchone()[0]
    diff = [c for c in cols if hashes["a"][c] != hashes["b"][c]]
    return {**res, "rows": [con.execute(f"SELECT count(*) FROM '{p.as_posix()}'").fetchone()[0] for p in (a, b)],
            "diff_cols": diff, "except_a_minus_b": ab, "except_b_minus_a": ba,
            "colhash_a": hashlib.sha256("".join(hashes["a"][c] for c in cols).encode()).hexdigest(),
            "colhash_b": hashlib.sha256("".join(hashes["b"][c] for c in cols).encode()).hexdigest(),
            "equal": not diff and ab == 0 and ba == 0}


def run_gated(score_args: list[str], runs_dir: Path = score.RUNS_DIR) -> dict:
    runs_dir = Path(runs_dir)
    A = _score(score_args, 1, runs_dir)
    gdir = runs_dir / "_gate" / A.name
    B = _score(score_args, 1, gdir / "B")
    C = _score(score_args, C_THREADS, gdir / "C")
    return verify(A, B, C, score_args)


def verify(A: Path, B: Path, C: Path, score_args=None) -> dict:
    """(i) + (ii) on existing run dirs; writes gate.json; renames A to +VOID on failure."""
    A, B, C = Path(A), Path(B), Path(C)
    gdir = B.parent
    check_i = {f: {"A": sha(A / f"{f}.parquet"), "B": sha(B / f"{f}.parquet")} for f in GATED}
    for v in check_i.values():
        v["equal"] = v["A"] == v["B"]
    check_ii = {f: row_equal(A / f"{f}.parquet", C / f"{f}.parquet") for f in GATED}
    ok_i = all(v["equal"] for v in check_i.values())
    ok_ii = all(v["equal"] for v in check_ii.values())
    verdict = {"gate": "determinism v1 (QA 2026-09-27)", "pass": ok_i and ok_ii, "check_i_byte_identical_threads1": ok_i,
               "check_ii_row_identical_threads4": ok_ii, "i": check_i, "ii": check_ii, "A": str(A), "B": str(B), "C": str(C),
               "C_threads": C_THREADS, "score_args": score_args, "checked_at": utcnow()}
    body = json.dumps(verdict, indent=1, sort_keys=True).encode()
    (A / "gate.json").write_bytes(body)
    (gdir / "gate.json").write_bytes(body)
    if not verdict["pass"]:
        void = A.with_name(A.name + "+VOID")
        A.rename(void)
        raise GateError(f"determinism gate FAILED (i={ok_i}, ii={ok_ii}); run marked VOID: {void}")
    return {**verdict, "gate_json_sha256": hashlib.sha256(body).hexdigest(),
            "run_json_sha256": sha(A / "run.json"), "dir": str(A)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    runs_dir = score.RUNS_DIR
    if "--runs-dir" in argv:
        i = argv.index("--runs-dir"); runs_dir = Path(argv[i + 1]); del argv[i:i + 2]
    if "--threads" in argv:
        raise SystemExit("the gate sets --threads itself")
    try:
        out = run_gated(argv, runs_dir)
    except GateError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps({k: out[k] for k in ("pass", "dir", "gate_json_sha256", "run_json_sha256")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
