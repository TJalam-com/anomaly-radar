# CRLF -> LF hash map, 2026-09-26 (Developer)

Cause: patch scripts used `Path.write_text` (CRLF on Windows). Normalised to LF at ~14:47Z. Verification rule: new LF bytes with `
`->`
` re-added must hash to the previously reported value.

## A. Unchanged since normalisation: re-adding CR reproduces the reported hash exactly

| path | reported (CRLF) sha256 | new (LF) sha256 | bytes CRLF | bytes LF | CR re-added = reported |
|---|---|---|---|---|---|
| `radar/config.py` | `4ded9286df415f5be19b474555acce8f3cfc3161e22b0bb6f244a23a9bf76cc9` | `95488d7ff083c5bd5db7d0af9e1eae7a8dbbf60176227fac0b2875054230a5b6` | 726 | 708 | yes |
| `radar/ingest.py` | `b0678628a7c696858f4b39699e932b204602440c1a27634e25c8b57735fee9db` | `e9e98f9f73ee963ecd47932fbb6b1420f430bbad0fc165d350aed17f4b00b3f0` | 16330 | 16011 | yes |
| `radar/schema.py` | `cebe29814c522cdf54a4debaf2b605ce9f66566ae50ed8fa96aa82bf6e9750fc` | `73f25ec7cd7b5678369926cd4883306b97bde57293a3dbadcbbe087a77afaa69` | 3063 | 3015 | yes |
| `radar/snapshots.py` | `e5b2b7fbc72b5c2ffe222f5cb77d2f01f655bfe06652615936973865e00ba9b2` | `8434d2970ccf805a9876863c054e41c178aff007944c1bd9b77f2b542df3f1f9` | 1704 | 1663 | yes |
| `radar/step4.py` | `dc4275d5f551a22be90895e2242974a89f3c83de50d26ffaaf3f46e3041da80b` | `c11046d6a44f1359c3a67e2428c6cf803094bc625e48919f44bd6e39e88c56ff` | 9420 | 9240 | yes |
| `tools/chain_fill_check.py` | `af86ee0a10b717b84cca4610d5335f64842158c146b92dff3dddf1d1b03a7a7c` | `ece6c4a4d0b8235359fb62c184284ef37ac1d5039e44ddfa937c48ba324b979c` | 8938 | 8755 | yes |

## B. Edited after the report (content changes, not only line endings): CR re-add cannot reproduce the old value

These were reported by prefix at 14:40Z (all CRLF then) and legitimately changed afterwards. Changes: split of frozen params from weights, S1 funding-window rule, S6 incomplete flag, frozen S8 shapes, profile truncation fields, cross-wallet hop cache, 3 workers + atomic writes, test and harness updates. Current LF hashes as of this file:

| path | reported 14:40Z (CRLF, prefix) | current (LF) sha256 | bytes LF |
|---|---|---|---|
| `radar/profile.py` | `9e1d4a40…` | `a3e8542424a3b7c6c0245b4cc3eef6b7d776eb58cb972051c7eab36e0239770c` | 21398 |
| `radar/score.py` | `3de757b3…` | `5ab8bd74ff63e88f311c4386a36423af3c16bde287974ab7597f0571a74d8e44` | 14383 |
| `radar/signals.py` | `277e7383…` | `549e35a4b750efd9487e118f63ed04516dd1e3008152240c5138fd4f21bfc959` | 19968 |
| `tests/g4_fixture.py` | `52ace4d5…` | `59e547ffc74c9b225afda60681929577a12b256c08df3e2d95981a02db87edc6` | 6132 |
| `tests/test_g4_score.py` | `cfd7b76d…` | `bf2e53df01be77b10eaf803f1072bbac958f05708cc8fa7c848a3074d1a0a6fd` | 8311 |
| `tools/break_g4.py` | `16034e7a…` | `8322760c1322d64f6e008b4a983103e2e0cd19938404e2ef9d79cd0e0766a274` | 5516 |

## C. Vendored pselamy file (`radar/vendor/pselamy_entity_data.py`)

The file = 6-line attribution header (always LF) + the upstream body. The body came from the Windows clone (git `core.autocrlf=true`), so it was CRLF.

| object | sha256 |
|---|---|
| reported file 14:40Z = header LF + body CRLF (5996 B) | `35b47c11a9e53bd4…` (reproduced from the current file by re-adding CR to the body lines only) |
| body, clone working copy (CRLF), as reported as the "source" | `79f3f0e68ccacc9b12f4f3198d4dc518a3e86e5590fdaebf9c7b5ca87a51b4f2` |
| body, upstream git blob `git show 302c6b99:…/entity_data.py` (LF) | `de4e3701a0be2b998f06fa85f865e4533c57959676ab7016569cb20d73600f22` |
| current file (LF throughout, 5842 B) | `098618f73f0324eb4fd018546cae0f514a1b3db7bbc73229d1aeefa4dbb82c39` |
| current body (`tail -n +7`) | `de4e3701…` = upstream blob, so the body is unmodified |

Future patches write bytes (UTF-8, LF) only.
