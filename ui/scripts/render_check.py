"""A6 render check: GET every page type of one run and fail if the served HTML shows a missing field as a JS literal.
Script bodies are stripped first (the Next.js runtime legitimately contains the word); everything else — text AND
attributes such as title="…" tooltips — is searched for the tokens below. Exit 1 on any hit, with page + context.
usage: python render_check.py <port> <data_dir> <run_id> [--max-wallets N]
       python render_check.py --self-test"""
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path

BAD = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]")
SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)


def hits(html: str):
    body = SCRIPT.sub("", html)
    return [body[max(0, m.start() - 60): m.end() + 60].replace("\n", " ") for m in BAD.finditer(body)]


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def self_test():
    bad = '<footer><span title="SNAP-FIXTURE undefined file undefined">x</span> weights: ? —</footer><script>var a=undefined</script>'
    ok = '<footer><span title="event times: not in this run">x</span></footer><script>if(x===undefined){}</script>'
    assert hits(bad), "planted 'undefined' in an attribute was not detected"
    assert not hits(ok), f"false positive: {hits(ok)}"
    assert hits("<td>NaN</td>") and hits("<p>[object Object]</p>")
    print("self-test ok (planted hits found, script-only 'undefined' ignored)")


def main(port, data_dir, run, max_wallets):
    d = Path(data_dir) / run
    q = f"?run={quote(run, safe='')}"   # "+" in a run id must be %2B, else it reads as a space (unknown run -> default)
    pages = ["/", "/markets", "/about", "/clusters", "/wallet", "/no-such-page", f"/wallet/0x{'0' * 39}f"]
    pages += [f"/wallet/{p.stem}" for p in sorted(d.glob("wallets/*.json"))[:max_wallets]]
    pages += [f"/market/{p.stem}" for p in sorted(d.glob("market_views/*.json"))]
    fails, n = [], 0
    for p in pages:
        code, html = get(port, p + q)
        n += 1
        if code not in (200, 404):
            fails.append((p, f"HTTP {code}"))
        if "unknown run id" in html:          # the run fell back to the default: the check would test the wrong run
            fails.append((p, "run id not recognised by the server (fallback to default run)"))
        for h in hits(html):
            fails.append((p, h))
    for c in sorted(d.glob("market_views/*.json"))[:3]:          # CSV provenance header (A4) is text too
        code, body = get(port, f"/api/market-nodes/{c.stem}{q}")
        n += 1
        if code != 200 or BAD.search(body):
            fails.append((f"/api/market-nodes/{c.stem}", f"HTTP {code} / {BAD.findall(body)[:3]}"))
    for p, h in fails:
        print(f"FAIL {p}: {h}")
    print(f"render_check: {n} responses, {len(fails)} failures (port {port}, run {run})")
    return 1 if fails else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test(); sys.exit(0)
    a = sys.argv[1:]
    mw = int(a[a.index("--max-wallets") + 1]) if "--max-wallets" in a else 40
    sys.exit(main(int(a[0]), a[1], a[2], mw))
