// N1 / A2 unit checks for lib/contrib.js (ESM loaded from a copy with an .mjs import path, no build needed).
// run: node scripts/test_contrib.mjs   (exit 1 on any failure)
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const tmp = path.join(here, "..", ".test-tmp");              // inside app/ui (D:), removed at the end
fs.mkdirSync(tmp, { recursive: true });
fs.copyFileSync(path.join(here, "..", "lib", "signals.js"), path.join(tmp, "signals.mjs"));
fs.writeFileSync(path.join(tmp, "contrib.mjs"), fs.readFileSync(process.env.CONTRIB_SRC || path.join(here, "..", "lib", "contrib.js"), "utf8").replace('from "./signals"', 'from "./signals.mjs"'));
const { breakdown, drivers, fmtWeight } = await import(pathToFileURL(path.join(tmp, "contrib.mjs")).href);

const W7 = { S1: 1 / 7, S2: 1 / 7, S3: 1 / 7, S4: 0, S5: 1 / 7, S6: 1 / 7, S7: 1 / 7, S8: 1 / 7 };
const sig = (c) => Object.fromEntries(Object.entries(c).map(([k, v]) => [k, v == null ? { component: null, na_reason: "x" } : { component: v, na_reason: null }]));
let fails = 0;
const t = (name, fn) => { try { fn(); console.log("PASS", name); } catch (e) { fails++; console.log("FAIL", name, "-", e.message); } };

// the QA N1 row: S2/S6/S7/S8 all at 1/7 (component 1.0) -> no tied signal dropped
t("4-way tie at the top: all tied listed, none in top", () => {
  const d = drivers(breakdown(sig({ S1: 0.89, S2: 1, S3: 0.38, S4: null, S5: 0.61, S6: 1, S7: 1, S8: 1 }), W7, 0).parts);
  assert.deepEqual(d.top.map((x) => x.sig), []);
  assert.deepEqual(d.tied.map((x) => x.sig), ["S2", "S6", "S7", "S8"]);
});
t("QA row 1: S2 = 0.99999999997 prints as 0.1429 like S6/S7/S8 -> tied", () => {
  const d = drivers(breakdown(sig({ S1: 0.8883651620370371, S2: 0.9999999999660203, S3: 0.377, S4: null, S5: 0.61, S6: 1, S7: 1, S8: 1 }), W7, 0).parts);
  assert.deepEqual(d.top.map((x) => x.sig), []);
  assert.deepEqual(d.tied.map((x) => x.sig), ["S2", "S6", "S7", "S8"]);
});
t("tie only at 2nd place: first kept in top, tied group listed", () => {
  const d = drivers(breakdown(sig({ S1: 0.9, S2: 0.5, S3: 0.5, S4: null, S5: 0.1, S6: 0.2, S7: null, S8: null }), W7, 0).parts);
  assert.deepEqual(d.top.map((x) => x.sig), ["S1"]);
  assert.deepEqual(d.tied.map((x) => x.sig), ["S2", "S3"]);
});
t("tie below 2nd place is not a tie for the display", () => {
  const d = drivers(breakdown(sig({ S1: 0.9, S2: 0.5, S3: 0.3, S4: null, S5: 0.3, S6: 0.1, S7: null, S8: null }), W7, 0).parts);
  assert.deepEqual(d.top.map((x) => x.sig), ["S1", "S2"]);
  assert.deepEqual(d.tied, []);
});
t("fewer than 2 positive products", () => {
  const d = drivers(breakdown(sig({ S1: 0, S2: 0.5, S3: 0, S4: null, S5: 0, S6: 0, S7: null, S8: null }), W7, 0).parts);
  assert.deepEqual(d.top.map((x) => x.sig), ["S2"]);
  assert.deepEqual(d.tied, []);
});
t("QA N1: a driver whose product prints as 0.0000 is never named", () => {
  const d = drivers(breakdown(sig({ S1: 0.5, S2: 0.0001, S3: 0, S4: null, S5: 0, S6: 0, S7: null, S8: null }), W7, 0).parts);
  assert.deepEqual(d.top.map((x) => x.sig), ["S1"]);                       // S2 = 1/7 x 0.0001 = 0.0000143 -> prints 0.0000
  assert.deepEqual(d.tied, []);
});
t("A2 mismatch flag", () => {
  const s = sig({ S1: 0.1, S2: 0.1, S3: 0.1, S4: null, S5: 0.1, S6: 0.1, S7: 0.1, S8: 0.1 });
  assert.equal(breakdown(s, W7, 0.1).mismatch, false);
  assert.equal(breakdown(s, W7, 0.95).mismatch, true);
});
t("fmtWeight fractions", () => { assert.equal(fmtWeight(1 / 7), "1/7"); assert.equal(fmtWeight(0.125), "1/8"); assert.equal(fmtWeight(0.1234567), "0.1235"); });

fs.rmSync(tmp, { recursive: true, force: true });
console.log(fails ? `${fails} FAILED` : "all passed");
process.exit(fails ? 1 : 0);
