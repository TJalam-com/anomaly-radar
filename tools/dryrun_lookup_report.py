"""PROF-002 dry-run lookup report (QA V1-3/V1-4): N_L after all hop-1 fetches, calls per lookup p50/p90/max (capped vs uncapped),
provider's accepted span, bisection trigger (result count vs block span), rate, and the projection to the PROF-001-scale lookup set.
Reads only the profile dir (manifest + lookups/*.json). usage: python prep_tools/dryrun_lookup_report.py <PROF-dir> [--project 19876]"""
import json
import math
import sys
from pathlib import Path


def q(v, f):
    """nearest-rank with a CEIL index: conservative for an upper bound on small samples (QA N4: p90 is the upper bound)."""
    v = sorted(v)
    return v[min(len(v) - 1, math.ceil(f * (len(v) - 1)))] if v else None


def report(prof: Path, project: int = 19_876) -> dict:
    man = json.loads((prof / "manifest.json").read_bytes())
    recs = [json.loads(f.read_bytes()) for f in sorted((prof / "lookups").glob("*.json"))]
    out = {"N_L_after_hop1": (man.get("lookups") or {}).get("lookup_set"), "lookups_recorded": len(recs),
           "calls_this_session": man.get("calls_this_session"), "http_429": (man.get("calls_this_session") or {}).get("http_429")}
    for name, sel in (("all", recs), ("capped", [r for r in recs if r["capped"]]), ("uncapped", [r for r in recs if not r["capped"]])):
        c = [r["calls"] for r in sel]
        out[f"calls_per_lookup_{name}"] = {"n": len(c), "p50": q(c, .5), "p90": q(c, .9), "max": max(c) if c else None}
    spans = [r["max_ok_span"] for r in recs if r.get("max_ok_span")]
    out["accepted_span_blocks"] = {"p50": q(spans, .5), "p90": q(spans, .9), "max": max(spans) if spans else None}
    sp = {k: sum(r["splits"][k] for r in recs) for k in ("result_count", "span", "other")}
    out["bisection_trigger"] = sp
    el = sum(r.get("elapsed_s", 0) for r in recs); calls = sum(r["calls"] for r in recs)
    out["rate"] = {"lookup_calls": calls, "elapsed_s": round(el, 1), "calls_per_s": round(calls / el, 2) if el else None}
    out["incomplete"] = sum(1 for r in recs if r["status"] != "ok")
    allc = [r["calls"] for r in recs]
    mean = (sum(allc) / len(allc)) if allc else None
    p50, p90 = out["calls_per_lookup_all"]["p50"], out["calls_per_lookup_all"]["p90"]
    rate = out["rate"]["calls_per_s"]          # the serial rate measured on the single RPC lease (one consumer at a time)
    n_l = project
    out["projection"] = {"N_L": n_l, "calls_per_lookup_mean": mean and round(mean, 3), "calls_per_lookup_p50": p50, "calls_per_lookup_p90": p90,
                         "calls_mean": mean and round(n_l * mean), "calls_p90_upper": p90 and n_l * p90, "calls_p50": p50 and n_l * p50,
                         "serial_calls_per_s": rate,
                         "hours_mean": (mean and rate) and round(n_l * mean / rate / 3600, 2),
                         "hours_p90_upper": (p90 and rate) and round(n_l * p90 / rate / 3600, 2),
                         "rule": "QA N4: N_L x mean calls per lookup; p90 = upper bound; p50 reported; hours at the serial rate on the single lease"}
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    proj = int(a[a.index("--project") + 1]) if "--project" in a else 19_876
    print(json.dumps(report(Path(a[0]), proj), indent=1))
