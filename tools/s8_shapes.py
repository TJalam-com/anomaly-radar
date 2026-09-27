"""S8 side-car for params v2 (QA ruling 2026-09-26): re-derive the first-redeem tx shape per (wallet, winning condition)
from PROF raw bundles, WITHOUT changing the PROF dir's manifested files.

  factory_direct : tx.to == Proxy Wallet Factory, selector 0x34ee9791, CREATE2(factory, keccak(tx.from), init-code hash) == wallet
                   (local computation, no RPC)
  relay_hub      : tx.to == Relay Hub, selector 0x405cec67; decode_ok = recovered signer == relayCall.from AND
                   CREATE2(recipient, keccak(from)) == wallet (one eth_call to the ecrecover precompile per tx)
  others         : as classified by radar.profile.classify_shape
Writes <PROF>/s8check/{shapes_v2.parquet, manifest.json}; refuses if s8check/manifest.json already exists.
Usage: python tools/s8_shapes.py --prof data/profiles/PROF-001 --params config/params_frozen_2026-09-27_v2.toml
"""
import argparse
import hashlib
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
from radar import profile  # noqa: E402

_spec = importlib.util.spec_from_file_location("relay_decode", APP / "tools" / "relay_decode.py")
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)

FACTORY = "0xab45c5a4b0c941a2f231c04c3f49182e1a254052"


def classify_v2(tx: dict, wallet: str, decode):
    """-> (shape_v2, decode_ok). `decode(tx)` returns (signer_ok, create2_ok) for relay_hub txs."""
    w = wallet.lower()
    to, sel, frm = (tx.get("to") or "").lower(), (tx.get("input") or "")[:10], (tx.get("from") or "").lower()
    if to == FACTORY and sel == "0x34ee9791":
        return ("factory_direct" if rd.create2_proxy(FACTORY, frm) == w else "other"), None
    base = profile.classify_shape(tx, wallet)
    if base == "relay_hub":
        sok, cok = decode(tx)
        return "relay_hub", bool(sok and cok)
    return base, None


def relay_decode(tx: dict, wallet: str):
    inp = bytes.fromhex(tx["input"][2:])
    frm, rec, enc, fee, gp, gl, nonce, sig, appr = rd.decode_relay_call(inp)
    packed = (b"rlx:" + bytes.fromhex(frm[2:]) + bytes.fromhex(rec[2:]) + enc + fee.to_bytes(32, "big") + gp.to_bytes(32, "big")
              + gl.to_bytes(32, "big") + nonce.to_bytes(32, "big") + bytes.fromhex(rd.HUB[2:]) + bytes.fromhex(tx["from"][2:]))
    signer = rd.recover(rd.k(b"\x19Ethereum Signed Message:\n32" + rd.k(packed)), sig) if len(sig) == 65 else None
    return signer == frm, rd.create2_proxy(rec, frm) == wallet.lower()


def build(prof: Path, params_path: Path, decode=None):
    out = prof / "s8check"
    if (out / "manifest.json").exists():
        raise SystemExit(f"{out} already finalised; never overwritten")
    praw = params_path.read_bytes()
    cfg = tomllib.loads(praw.decode("utf-8"))
    assert cfg["fetch"]["proxy_init_code_hash"].lower() == "0x" + rd.PROXY_INIT_CODE_HASH.hex(), "init-code hash mismatch with params"
    rows = []
    for f in sorted((prof / "raw").glob("*.json")):
        b = json.loads(f.read_bytes())
        w = b["wallet"]
        for cond, e in b["redeems"].items():
            tx = e.get("first_tx")
            if not tx or "error" in tx:
                continue
            shape, ok = classify_v2(tx, w, decode or (lambda t: relay_decode(t, w)))
            rows.append((w, cond, tx.get("hash"), shape, ok))
    out.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({"proxy_wallet": [r[0] for r in rows], "condition_id": [r[1] for r in rows], "tx_hash": [r[2] for r in rows],
                    "shape_v2": [r[3] for r in rows], "decode_ok": pa.array([r[4] for r in rows], pa.bool_())})
    pq.write_table(tbl, out / "shapes_v2.parquet")
    counts = {}
    for r in rows:
        key = f"{r[3]}|decode_ok={r[4]}" if r[3] == "relay_hub" else r[3]
        counts[key] = counts.get(key, 0) + 1
    body = json.dumps({"prof_manifest_sha256": hashlib.sha256((prof / "manifest.json").read_bytes()).hexdigest(),
                       "params_file_sha256": hashlib.sha256(praw).hexdigest(), "rows": len(rows), "counts": counts,
                       "derived": {"file": "shapes_v2.parquet", "sha256": hashlib.sha256((out / "shapes_v2.parquet").read_bytes()).hexdigest()},
                       "rpc_calls": rd.calls["rpc"]}, indent=1, sort_keys=True).encode()
    (out / "manifest.json").write_bytes(body)
    return {"manifest_sha256": hashlib.sha256(body).hexdigest(), "rows": len(rows), "counts": counts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prof", required=True)
    ap.add_argument("--params", required=True)
    a = ap.parse_args()
    print(json.dumps(build(Path(a.prof), Path(a.params)), indent=1))
