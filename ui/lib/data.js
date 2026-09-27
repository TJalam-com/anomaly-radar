// Server-only data access. Reads exported UI views (scripts/export_ui_data.py; fixtures from scripts/make_fixture.py),
// one directory per run id under data/; read-only. Exports physically lack total_scorer / profiled / bypass (G6 U7, O5).
import "server-only";
import fs from "node:fs";
import path from "node:path";

// data root: "data" for the review server (:3000); "data-dev" for iteration (:3001) — never shared while iterating
const DATA_ROOT = path.join(process.cwd(), process.env.ANOMALY_RADAR_DATA || "data");
const ADDR = /^0x[0-9a-f]{40}$/;
const COND = /^0x[0-9a-f]{64}$/;
const RUNID = /^[A-Za-z0-9._+-]{1,120}$/;

function readIn(run, name) {
  return JSON.parse(fs.readFileSync(path.join(DATA_ROOT, run, name), "utf8"));
}

export function runs() {
  return fs.readdirSync(DATA_ROOT, { withFileTypes: true })
    .filter((d) => d.isDirectory() && RUNID.test(d.name) && fs.existsSync(path.join(DATA_ROOT, d.name, "context.json")))
    .map((d) => { const c = readIn(d.name, "context.json"); return { id: d.name, fixture: !!c.fixture, banner: c.banner, label: c.run_label || d.name }; })
    .sort((a, b) => (a.fixture - b.fixture) || a.id.localeCompare(b.id));
}

export function defaultRun() {
  const f = path.join(DATA_ROOT, "DEFAULT_RUN");
  const all = runs();
  const want = fs.existsSync(f) ? fs.readFileSync(f, "utf8").trim() : null;
  const hit = all.find((r) => r.id === want) || all.find((r) => !r.fixture) || all[0];
  if (!hit) throw new Error("no exported run");
  return hit.id;
}

/** Resolve the ?run= query value to a known run id (unknown values fall back to the default run). */
export function resolveRun(q) {
  const ids = runs().map((r) => r.id);
  return typeof q === "string" && ids.includes(q) ? q : defaultRun();
}

/** Link helper: keep a non-default run selector on every internal link. */
export function href(p, run) {
  if (!run || run === defaultRun()) return p;
  return p + (p.includes("?") ? "&" : "?") + "run=" + encodeURIComponent(run);
}

export function context(run) {
  const ctx = readIn(run, "context.json");
  const manifest = readIn(run, "manifest.json");
  return { ...ctx, run_dir: run, export_manifest: manifest.files };
}

export function leaderboard(run, scope) {
  if (scope !== "all") throw new Error("scope not exported in this run");
  return readIn(run, "leaderboard_all.json");
}

export function markets(run) {
  return readIn(run, "markets.json");
}

export function wallet(run, addr) {
  const a = String(addr || "").toLowerCase();
  if (!ADDR.test(a)) return null;
  const f = path.join(DATA_ROOT, run, "wallets", `${a}.json`);
  return fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, "utf8")) : null;
}

export function hasWalletPage(run, addr) {
  return ADDR.test(String(addr || "")) && fs.existsSync(path.join(DATA_ROOT, run, "wallets", `${addr}.json`));
}

export function prices(run, cond) {
  if (!COND.test(String(cond || ""))) return null;
  const f = path.join(DATA_ROOT, run, "prices", `${cond}.json`);
  return fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, "utf8")) : null;
}

export function marketView(run, cond) {
  if (!COND.test(String(cond || ""))) return null;
  const f = path.join(DATA_ROOT, run, "market_views", `${cond}.json`);
  return fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, "utf8")) : null;
}

export const isAddress = (a) => ADDR.test(String(a || ""));

/** V4 ego view: links/<addr>.json (linked wallets + the wallet's own shared counterparties); null when S6 is NA. */
export function walletLinks(run, addr) {
  const a = String(addr || "").toLowerCase();
  if (!ADDR.test(a)) return null;
  const f = path.join(DATA_ROOT, run, "links", `${a}.json`);
  return fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, "utf8")) : null;
}

/** complete shared counterparty set of a link, from both wallets' own lists; stop-listed addresses are dropped here too
 *  (the export already excludes them: defence in depth, and the UI Tester's planted stop-listed address must not show). */
export function sharedCounterparties(a, b, stopList = []) {
  if (!a || !b) return [];
  const stop = new Set(stopList);
  const side = (L) => { const m = new Map(); for (const [c, d, h] of L.cps) if (!stop.has(c)) (m.get(c) || m.set(c, []).get(c)).push([d, h]); return m; };
  const A = side(a), B = side(b);
  const out = [];
  for (const [c, sa] of A) if (B.has(c)) out.push({ c, a: sa, b: B.get(c), hop: Math.min(...sa.map((x) => x[1])) + Math.min(...B.get(c).map((x) => x[1])) });
  return out.sort((x, y) => x.hop - y.hop || x.c.localeCompare(y.c));
}
