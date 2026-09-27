"""T-01 soak test: N sequential GETs over wallet + market pages (largest first) against one server; records every
status, and samples the server process RSS (by listening port) every 25 requests. Pass = 0 statuses outside
{200, 404}, and RSS bounded (max RSS <= 2x the RSS after the first 25 requests + 200 MB).
usage: python soak.py <port> <data_dir/run_id> [N=300]"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def rss_mb(port):
    ps = ("$p=(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -First 1; "
          "(Get-Process -Id $p).WorkingSet64" % port)
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    return int(out) / 1e6 if out.isdigit() else None


def main(port, run_dir, n):
    d = Path(run_dir)
    wallets = sorted(d.glob("wallets/*.json"), key=lambda p: -p.stat().st_size)
    markets = sorted(d.glob("market_views/*.json"), key=lambda p: -p.stat().st_size)
    urls = []
    i = 0
    while len(urls) < n:
        urls.append(f"/wallet/{wallets[i % len(wallets)].stem}")
        urls.append(f"/market/{markets[i % len(markets)].stem}")
        i += 1
    urls = urls[:n]
    statuses, samples, t0 = {}, [], time.time()
    for k, u in enumerate(urls, 1):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{u}", timeout=60) as r:
                code = r.status
                r.read()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:                     # connection errors count as failures
            code = f"ERR {type(e).__name__}"
        statuses[str(code)] = statuses.get(str(code), 0) + 1
        if k % 25 == 0:
            samples.append({"after": k, "rss_mb": rss_mb(port), "t_s": round(time.time() - t0, 1)})
    bad = sum(v for c, v in statuses.items() if c not in ("200", "404"))
    base = samples[0]["rss_mb"] if samples and samples[0]["rss_mb"] else None
    peak = max((s["rss_mb"] or 0) for s in samples) if samples else None
    bounded = base is not None and peak <= 2 * base + 200
    res = {"port": port, "requests": len(urls), "statuses": statuses, "non_2xx_404": bad, "rss_samples": samples,
           "rss_first_mb": base, "rss_peak_mb": peak, "rss_bounded": bounded, "pass": bad == 0 and bounded,
           "seconds": round(time.time() - t0, 1)}
    print(json.dumps(res, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 300))
