// Complete node table of one market as CSV (QA ruling (d): for the UI Tester). Read-only; same data as the page.
import { context, marketView, resolveRun } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET(req, { params }) {
  const { condition } = await params;
  const run = resolveRun(new URL(req.url).searchParams.get("run"));
  const v = marketView(run, condition);
  if (!v) return new Response("not found\n", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
  const esc = (x) => (x == null ? "" : String(x));
  const ctx = context(run);
  const snap = ctx.snapshots.find((x) => x.id === "SNAP-003") || ctx.snapshots[0];
  const step4 = ctx.snapshots.find((x) => x.id.endsWith("step4"));
  const trust = ctx.g5_passed_weights_sha256 && ctx.g5_passed_weights_sha256 === ctx.weights_sha256 ? "validated" : "unvalidated (detector not yet validated)";
  const lines = [   // QA A4: provenance as header comment lines
    `# Anomaly Radar market node export; market ${condition}`,
    `# run id ${ctx.run_id}; export instant ${new Date().toISOString()} (UTC)`,
    `# snapshot ${snap?.id} ${snap?.manifest_sha256 ?? "not in this run"}; step-4 ${step4?.manifest_sha256 ?? "not in this run"}`,
    `# weights ${ctx.weights_version ?? "not in this run"} file ${ctx.weights_file_sha256 ?? "not in this run"}; params ${ctx.params_file_sha256 ?? "not in this run"}`,
    `# profile ${ctx.profile_label ?? "none"}; trust state ${trust}`,
    "# scores are probabilistic indicators of anomalous trading patterns, not proof, and make no claim about who controls a wallet",
    "# NA = no data for the signal; NA is not 0",
    "wallet,net_shares_at_t_ref,shares_by_outcome,anomalous_pattern_score_this_market,prefilter,in_graph"];
  const g = new Set(v.graph_wallets);
  for (const n of v.nodes) {
    const by = Object.entries(n.shares_by_outcome).map(([k, s]) => `${k}:${s}`).join(" ");
    lines.push([n.wallet, n.shares, by, n.score == null ? "NA" : n.score, n.prefilter_pass == null ? "" : n.prefilter_pass ? "pass" : "fail", g.has(n.wallet) ? 1 : 0].map(esc).join(","));
  }
  return new Response(lines.join("\n") + "\n", { headers: { "content-type": "text/csv; charset=utf-8",
    "content-disposition": `attachment; filename="market-nodes-${condition.slice(0, 10)}-${ctx.run_id.replace(/[^A-Za-z0-9_.-]/g, "_")}.csv"` } });
}
