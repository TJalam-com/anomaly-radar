"""Pre-registered weight search (design notes r5 Δ3 steps 1–4/6–7, r6 Δ1 bootstrap), re-runnable end-to-end (QA P12).

python -m radar.weights_search --snap <SNAP> --params <frozen.toml> --prof <PROF> --look <look.csv> --look-sha <sha256>
       --wallet-col <h> --label-col <h> --unit-col <h> --pos <value> --neg <value> [--bypass-list f] [--event-times f]
       [--out config/weights.toml] [--B 2000] [--seed 20260926]

1. One scoring pass with W0 (equal weights over signals computable in scope 'all') -> dense components.
2. Grid (fixed): W0, leave-one-out per computable signal, double-one per computable signal (renormalised).
3. Objective: scorer-only AUC over scope 'all', look positives vs look negatives present in U(all) (ties = 1/2);
   pipeline AUC (non-passed wallets rank below every scored wallet) reported, never used to select.
4. Paired bootstrap: B resamples of positive UNITS (cluster: all wallets of a unit move together) and negative units
   (unit column as supplied; negatives are single-wallet units per the split), equal counts, fixed seed.
   Select W != W0 only if the 5th percentile of dAUC(W - W0) > 0; best 5th pct wins; tie -> smallest L1 to W0; else W0.
5. Every config is logged (JSONL) BEFORE selection; chosen weights written to --out with a .lock; all input hashes logged.
6. Determinism gate (QA 2026-09-27): the W0 pass is a gated score run ((i)+(ii), radar.gate); the selection runs twice
   in separate processes and JSONL + weights + lock must be byte-identical, else the selection is VOID.
Weights chosen on SNAP-003 are PROVISIONAL until held-out gate G-HO-2 clears (QA). The held-out half is never an input.
"""
import argparse
import csv
import hashlib
import io
import json
import random
import subprocess
import sys
from pathlib import Path

import duckdb

from radar import gate, score, signals


class SearchError(RuntimeError):
    pass


def auc(pos, neg):
    """P(pos > neg) with ties 1/2; pos/neg are lists of floats (None = ranks below everything)."""
    if not pos or not neg:
        return None
    lo = float("-inf")
    s = 0.0
    for p in pos:
        p = lo if p is None else p
        for n in neg:
            n = lo if n is None else n
            s += 1.0 if p > n else 0.5 if p == n else 0.0
    return s / (len(pos) * len(neg))


def grid(computable):
    base = {s: (1.0 if s in computable else 0.0) for s in signals.SIGNALS}
    cfgs = [("W0", base)]
    for s in computable:
        cfgs.append((f"LOO_{s}", {**base, s: 0.0}))
    for s in computable:
        cfgs.append((f"DBL_{s}", {**base, s: 2.0}))
    out = []
    for name, w in cfgs:
        tot = sum(w.values())
        if tot > 0:
            out.append((name, {k: v / tot for k, v in w.items()}))
    return out


def read_look(path: Path, sha_expected: str, wcol, lcol, ucol, pos_v, neg_v):
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != sha_expected:
        raise SearchError(f"look file sha {sha} != expected {sha_expected}")
    rd = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    for c in (wcol, lcol, ucol):
        if c not in (rd.fieldnames or []):
            raise SearchError(f"look file lacks column {c!r} (no guessing)")
    rows = []
    for r in rd:
        lab = r[lcol].strip()
        if lab not in (pos_v, neg_v):
            raise SearchError(f"unexpected label {lab!r} (allowed {pos_v!r}/{neg_v!r})")
        rows.append((r[wcol].strip().lower(), lab == pos_v, r[ucol].strip()))
    return rows, {"path": str(path), "sha256": sha, "rows": len(rows)}


def totals(con, w):
    cases = " ".join(f"WHEN '{s}' THEN {w[s]!r}" for s in signals.SIGNALS)
    # fixed summation order (QA determinism ruling): same list_sum(ORDER BY signal_id) as score.score
    return dict(con.execute(f"""SELECT d.proxy_wallet, list_sum(list(CASE WHEN d.component IS NOT NULL
                                    THEN (CASE d.signal_id {cases} END) * d.component ELSE 0 END ORDER BY d.signal_id))
        FROM sig d WHERE d.scope = 'all' GROUP BY 1""").fetchall())


GATED_OUT = ("weights_search.jsonl", "weights.toml", "weights.toml.lock")


def select(base_dir: Path, look, look_sha, cols, labels, B, seed, out_dir: Path) -> dict:
    """Grid + paired bootstrap on a GATED base run. Writes weights_search.jsonl, weights.toml, weights.toml.lock to out_dir.
    Every byte written is a function of (base run files, look file, B, seed): no run ids, paths or clock (gate (i))."""
    rows, look_meta = read_look(Path(look), look_sha, *cols, *labels)
    d = Path(base_dir)
    rj = json.loads((d / "run.json").read_bytes())
    gj = d / "gate.json"
    if not gj.exists() or not json.loads(gj.read_bytes())["pass"]:
        raise SearchError(f"base run {d} has no passing determinism gate (gate.json)")
    base_sig_sha = {x["file"]: x["sha256"] for x in rj["derived"]}["signals.parquet"]
    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute(f"CREATE TABLE sig AS SELECT * FROM '{(d / 'signals.parquet').as_posix()}' WHERE scope = 'all'")
    passed = dict(con.execute(f"SELECT proxy_wallet, passed_prefilter FROM '{(d / 'universe.parquet').as_posix()}' WHERE scope = 'all'").fetchall())
    computable = rj["computable_signals"]
    present = [(w, p, u) for w, p, u in rows if w in passed]
    absent = len(rows) - len(present)
    units = {}
    for w, p, u in present:
        units.setdefault((p, u), []).append(w)
    pos_units = [ws for (p, u), ws in units.items() if p]
    neg_units = [ws for (p, u), ws in units.items() if not p]
    cfgs = grid(computable)
    tot = {name: totals(con, w) for name, w in cfgs}

    def auc_for(name, pu, nu, pipeline=False):
        t = tot[name]
        val = (lambda w: t.get(w) if passed.get(w) else None) if pipeline else (lambda w: t.get(w))
        return auc([val(w) for ws in pu for w in ws], [val(w) for ws in nu for w in ws])

    rng = random.Random(seed)
    samples = [([rng.choice(pos_units) for _ in pos_units], [rng.choice(neg_units) for _ in neg_units]) for _ in range(B)] \
        if pos_units and neg_units else []
    w0_boot = [auc_for("W0", pu, nu) for pu, nu in samples]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    log_path = out_dir / "weights_search.jsonl"
    entries = []
    with open(log_path, "wb") as fh:
        for name, w in cfgs:
            deltas = sorted(auc_for(name, pu, nu) - a0 for (pu, nu), a0 in zip(samples, w0_boot)) if samples else []
            pct = (lambda q: deltas[min(len(deltas) - 1, int(q * len(deltas)))] if deltas else None)
            e = {"config": name, "weights": w, "auc_scorer": auc_for(name, pos_units, neg_units),
                 "auc_pipeline": auc_for(name, pos_units, neg_units, pipeline=True),
                 "boot": {"B": len(samples), "seed": seed, "mean_dAUC": (sum(deltas) / len(deltas)) if deltas else None,
                          "p05": pct(0.05), "p50": pct(0.50), "p95": pct(0.95)},
                 "n_pos_units": len(pos_units), "n_neg_units": len(neg_units), "look_absent_from_U": absent,
                 "look_sha256": look_meta["sha256"], "params_file_sha256": rj["params_file_sha256"],
                 "base_signals_sha256": base_sig_sha}
            entries.append(e)
            fh.write((json.dumps(e, sort_keys=True) + "\n").encode())   # logged before selection
    qual = [e for e in entries if e["config"] != "W0" and e["boot"]["p05"] is not None and e["boot"]["p05"] > 0]
    w0 = dict(cfgs)["W0"]
    if qual:
        best = max(qual, key=lambda e: (e["boot"]["p05"], -sum(abs(e["weights"][s] - w0[s]) for s in signals.SIGNALS)))
    else:
        best = [e for e in entries if e["config"] == "W0"][0]
    log_sha = hashlib.sha256(log_path.read_bytes()).hexdigest()
    body = (f"version = 'search-{base_sig_sha[:16]}-{best['config']}'\n# PROVISIONAL until G-HO-2 clears. log sha256 {log_sha}\n[weights]\n"
            + "".join(f"{s} = {best['weights'][s]!r}\n" for s in signals.SIGNALS)).encode()
    (out_dir / "weights.toml").write_bytes(body)
    (out_dir / "weights.toml.lock").write_bytes(hashlib.sha256(body).hexdigest().encode())
    return {"chosen": best["config"], "weights": best["weights"], "log_sha256": log_sha, "look": look_meta,
            "weights_file_sha256": hashlib.sha256(body).hexdigest()}


def _select_sub(base_dir, look, look_sha, cols, labels, B, seed, out_dir) -> dict:
    """one selection pass in a fresh process: process-level nondeterminism (hash seed, unseeded RNG) reaches gate (i)."""
    root = Path(score.__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-B", "-m", "radar.weights_search", "--select-from", str(base_dir), "--select-out", str(out_dir),
                        "--look", str(look), "--look-sha", look_sha, "--wallet-col", cols[0], "--label-col", cols[1], "--unit-col", cols[2],
                        "--pos", labels[0], "--neg", labels[1], "--B", str(B), "--seed", str(seed)], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise SearchError(f"selection subprocess failed rc={r.returncode}: {r.stderr[-800:]}")
    return json.loads(r.stdout[r.stdout.index("{"):])


def run_search(snap, params, prof, look, look_sha, cols, labels, bypass=None, event_times=None, B=2000, seed=20260926,
               out_weights: Path | None = None, runs_dir: Path | None = None, params_lock=None):
    runs_dir = Path(runs_dir or score.RUNS_DIR)
    read_look(Path(look), look_sha, *cols, *labels)              # refuse a wrong sha / column before any scoring
    # 1) W0 scoring pass under the determinism gate (i)+(ii); equal weights, all-NA signals zeroed inside score.score
    wdir = runs_dir / "_w0"
    wdir.mkdir(parents=True, exist_ok=True)
    tmpw = wdir / "w0_equal.toml"
    tmpw.write_bytes(("version = 'W0-equal'\n[weights]\n" + "".join(f"{s} = 1.0\n" for s in signals.SIGNALS)).encode())
    (wdir / "w0_equal.toml.lock").write_bytes(hashlib.sha256(tmpw.read_bytes()).hexdigest().encode())
    sargs = ["--snap", str(snap), "--weights", str(tmpw), "--params", str(params), "--tag", "W0_for_search"]
    for flag, v in (("--params-lock", params_lock), ("--prof", prof), ("--bypass-list", bypass), ("--event-times", event_times)):
        if v:
            sargs += [flag, str(v)]
    base = gate.run_gated(sargs, runs_dir)
    bdir = Path(base["dir"])
    # 2) selection twice, separate processes; JSONL + weights + lock must be byte-identical (gate (i)), else VOID
    sdir = runs_dir / "_search" / bdir.name
    ra = _select_sub(bdir, look, look_sha, cols, labels, B, seed, sdir / "A")
    _select_sub(bdir, look, look_sha, cols, labels, B, seed, sdir / "B")
    check = {f: {"A": hashlib.sha256((sdir / "A" / f).read_bytes()).hexdigest(),
                 "B": hashlib.sha256((sdir / "B" / f).read_bytes()).hexdigest()} for f in GATED_OUT}
    ok = all(v["A"] == v["B"] for v in check.values())
    verdict = {"gate": "determinism v1 (QA 2026-09-27): selection check (i)", "pass": ok, "files": check, "base_run": str(bdir)}
    (sdir / "gate.json").write_bytes(json.dumps(verdict, indent=1, sort_keys=True).encode())
    if not ok:
        (sdir / "A").rename(sdir / "A+VOID")
        raise SearchError(f"weight-search determinism gate FAILED; selection VOID: {sdir}")
    result = {**ra, "base_run_id": bdir.name, "log": str(sdir / "A" / "weights_search.jsonl"), "selection_gate": verdict,
              "provisional": "until held-out gate G-HO-2 clears (QA)"}
    if out_weights:
        Path(out_weights).write_bytes((sdir / "A" / "weights.toml").read_bytes())
        Path(str(out_weights) + ".lock").write_bytes((sdir / "A" / "weights.toml.lock").read_bytes())
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("--look", "--look-sha", "--wallet-col", "--label-col", "--unit-col", "--pos", "--neg"):
        ap.add_argument(a, required=True)
    ap.add_argument("--snap"); ap.add_argument("--params")
    ap.add_argument("--prof"); ap.add_argument("--bypass-list"); ap.add_argument("--event-times"); ap.add_argument("--out")
    ap.add_argument("--B", type=int, default=2000); ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--select-from", help=argparse.SUPPRESS); ap.add_argument("--select-out", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    cols, labels = (a.wallet_col, a.label_col, a.unit_col), (a.pos, a.neg)
    if a.select_from:                                             # internal: one selection pass (run twice by run_search)
        r = select(Path(a.select_from), a.look, a.look_sha, cols, labels, a.B, a.seed, Path(a.select_out))
    else:
        if not (a.snap and a.params):
            ap.error("--snap and --params are required")
        pp = Path(a.params)
        r = run_search(a.snap, a.params, a.prof, a.look, a.look_sha, cols, labels, a.bypass_list, a.event_times, a.B, a.seed,
                       Path(a.out) if a.out else None, params_lock=pp.with_suffix(pp.suffix + ".lock"))
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    sys.exit(main())
