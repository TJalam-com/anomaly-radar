"""S8 prerequisite (QA Q2): is a relay_hub REDEEM signed by the proxy wallet's owner?

For each sampled relay_hub REDEEM tx (PROF-001 redeems, fixed seed):
  1. decode RelayHub.relayCall(from, recipient, encodedFunction, transactionFee, gasPrice, gasLimit, nonce, signature, approvalData)
  2. GSN v1 relay hash = keccak256(abi.encodePacked("rlx:", from, recipient, encodedFunction, transactionFee, gasPrice,
     gasLimit, nonce, relayHub, relay)) with relay = tx.from; signed message = keccak256("\\x19Ethereum Signed Message:\\n32" || hash)
  3. recover the signer with the ecrecover precompile (eth_call to 0x...01) -> must equal `from`
  4. CREATE2 check: proxy = keccak256(0xff || recipient(factory) || keccak256(from) || keccak256(EIP-1167 init code for the
     wallet's implementation))[12:] -> must equal the redeeming proxy wallet
Outcome per tx: signer_ok, create2_ok. relay_hub counts as user-signed only if both hold on the whole sample (QA: >= 30).
Usage: python tools/relay_decode.py <n> <out.json>
"""
import json
import random
import sys

import duckdb
import httpx
from Crypto.Hash import keccak

RPC = "https://polygon.gateway.tenderly.co"
HUB = "0xd216153c06e857cd7f72665e0af1d7d82172f494"


def k(b: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(b)
    return h.digest()


assert k(b"hello").hex() == "1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"
cli = httpx.Client(timeout=60)
calls = {"rpc": 0}


def rpc(m, p):
    calls["rpc"] += 1
    r = cli.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p})
    if r.status_code == 429:
        raise SystemExit("HTTP 429: stopping")
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]


def u256(b):
    return int.from_bytes(b, "big")


def decode_relay_call(inp: bytes):
    a = inp[4:]
    w = lambda i: a[32 * i:32 * i + 32]
    frm = "0x" + w(0)[12:].hex()
    rec = "0x" + w(1)[12:].hex()
    fee, gp, gl, nonce = u256(w(3)), u256(w(4)), u256(w(5)), u256(w(6))

    def dyn(i):
        off = u256(w(i))
        ln = u256(a[off:off + 32])
        return a[off + 32:off + 32 + ln]
    return frm, rec, dyn(2), fee, gp, gl, nonce, dyn(7), dyn(8)


def recover(msg_hash: bytes, sig: bytes):
    r, s, v = sig[:32], sig[32:64], sig[64]
    if v < 27:
        v += 27
    data = "0x" + msg_hash.hex() + v.to_bytes(32, "big").hex() + r.hex() + s.hex()
    out = rpc("eth_call", [{"to": "0x0000000000000000000000000000000000000001", "data": data}, "latest"])
    return "0x" + out[-40:] if out and len(out) >= 42 else None


# Proxy Wallet Factory init-code hash as used by Polymarket's client libraries (value recalled, not read from source);
# accepted only because it reproduces sampled wallet addresses exactly (create2_ok would be False otherwise).
PROXY_INIT_CODE_HASH = bytes.fromhex("d21df8dc65880a8606f09fe0ce3df9b8869287ab0b058be05aa9e8af6330a00b")


def create2_proxy(factory: str, owner: str, impl: str | None = None):
    salt = k(bytes.fromhex(owner[2:]))
    return "0x" + k(b"\xff" + bytes.fromhex(factory[2:]) + salt + PROXY_INIT_CODE_HASH)[12:].hex()


def main(n, out_path):
    con = duckdb.connect()
    rows = con.execute("SELECT tx_hash, proxy_wallet FROM 'data/profiles/PROF-001/derived/redeems.parquet' WHERE shape = 'relay_hub' ORDER BY tx_hash").fetchall()
    rng = random.Random(20260927)
    sample = rng.sample(rows, min(n, len(rows)))
    res = []
    for tx, wallet in sample:
        t = rpc("eth_getTransactionByHash", [tx])
        inp = bytes.fromhex(t["input"][2:])
        frm, rec, enc, fee, gp, gl, nonce, sig, appr = decode_relay_call(inp)
        packed = (b"rlx:" + bytes.fromhex(frm[2:]) + bytes.fromhex(rec[2:]) + enc + fee.to_bytes(32, "big") + gp.to_bytes(32, "big")
                  + gl.to_bytes(32, "big") + nonce.to_bytes(32, "big") + bytes.fromhex(HUB[2:]) + bytes.fromhex(t["from"][2:]))
        h = k(packed)
        signer = recover(k(b"\x19Ethereum Signed Message:\n32" + h), sig) if len(sig) == 65 else None
        code = rpc("eth_getCode", [wallet, "latest"])
        impl = "0x" + code[22:62] if code.startswith("0x363d3d373d3d3d363d73") else None
        c2 = create2_proxy(rec, frm)
        res.append({"tx": tx, "wallet": wallet, "relay": t["from"].lower(), "from": frm, "recipient": rec, "inner_selector": "0x" + enc[:4].hex(),
                    "sig_len": len(sig), "signer": signer, "signer_ok": signer == frm, "impl": impl, "create2": c2,
                    "create2_ok": c2 == wallet.lower() if c2 else None})
        print(json.dumps({k2: res[-1][k2] for k2 in ("tx", "signer_ok", "create2_ok", "inner_selector")}), flush=True)
    summary = {"n": len(res), "signer_ok": sum(r["signer_ok"] for r in res), "create2_ok": sum(bool(r["create2_ok"]) for r in res),
               "both_ok": sum(r["signer_ok"] and bool(r["create2_ok"]) for r in res), "recipients": sorted({r["recipient"] for r in res}),
               "inner_selectors": sorted({r["inner_selector"] for r in res}), "rpc_calls": calls["rpc"], "seed": 20260927,
               "distinct_wallets": len({r["wallet"] for r in res}), "distinct_signers": len({r["signer"] for r in res}),
               "max_wallets_per_signer": max((sum(1 for x in res if x["signer"] == s0) for s0 in {r["signer"] for r in res}), default=0),
               "distinct_relays": len({r["relay"] for r in res})}
    json.dump({"summary": summary, "rows": res}, open(out_path, "w"), indent=1)
    print("SUMMARY", json.dumps(summary))


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2])
