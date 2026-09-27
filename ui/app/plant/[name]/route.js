// FIXTURE-ONLY plant endpoints for the UI Tester's C0-11 crawler controls. They serve content from the fixture run's
// context.plants (data files, never source) and return 404 for every non-fixture run, so real-run pages never reach them.
import { context, resolveRun } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET(req, { params }) {
  const { name } = await params;
  const run = resolveRun(new URL(req.url).searchParams.get("run"));
  const ctx = context(run);
  if (!ctx.fixture) return new Response("not found", { status: 404 });
  const p = ctx.plants || {};
  if (name === "lazy" && p.lazy_chunk) {
    const js = `/* fixture lazy chunk (C0-11 plant 1) */ window.__plant_lazy = ${JSON.stringify(p.lazy_chunk)};` +
      ` fetch("/plant/api?run=${encodeURIComponent(run)}").then(function(r){return r.text();});`;
    return new Response(js, { headers: { "content-type": "application/javascript; charset=utf-8" } });
  }
  if (name === "api" && p.api_body) {
    return new Response(JSON.stringify({ plant: "C0-11 plant 2", text: p.api_body }), { headers: { "content-type": "application/json" } });
  }
  return new Response("not found", { status: 404 });
}
