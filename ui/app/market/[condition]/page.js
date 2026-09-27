// V2 Market view + bubble map (G6 r6 §1 V2, C2, M1–M6; design D7; T-02/T-05/T-06/T-07/T-12).
// Volumes from the taker walk; Gamma only as a cross-check. Node table complete across pages + CSV export.
import { notFound } from "next/navigation";
import { context, hasWalletPage, href, marketView, markets, resolveRun } from "@/lib/data";
import { scoreColour } from "@/lib/colour";
import { fmtT, intParam, num, PAGE_SIZES } from "@/lib/format";
import BubbleGraph from "../../components/BubbleGraph";
import CopyAddress from "../../components/CopyAddress";
import Pager from "../../components/Pager";
import { NaBadge, PrefilterMarker, RunBar, S6IncompleteMarker, SourceFooter, TrustIcon } from "../../components/ui";

export const dynamic = "force-dynamic";

export default async function MarketPage({ params, searchParams }) {
  const { condition } = await params;
  const sp = await searchParams;
  if (!/^0x[0-9a-f]{64}$/.test(String(condition))) notFound();
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const mk = markets(run);
  const m = mk[condition];
  const v = marketView(run, condition);
  if (!m || !v) notFound();
  const graphSet = new Set(v.graph_wallets);
  const graphNodes = v.nodes.filter((n) => graphSet.has(n.wallet)).sort((a, b) => b.score - a.score || a.wallet.localeCompare(b.wallet));
  const rows = [...v.nodes].sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || b.shares - a.shares || a.wallet.localeCompare(b.wallet));
  const size = PAGE_SIZES.includes(Number(sp?.size)) ? Number(sp.size) : 50;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = intParam(sp?.page, { def: 1, min: 1, max: pages }).value;
  const shown = rows.slice((page - 1) * size, page * size);
  const outcomeName = (oi) => m.outcomes?.[oi] ?? oi;
  const listHref = (p, s) => href(`/market/${condition}?${new URLSearchParams({ ...(p > 1 ? { page: String(p) } : {}), ...(s !== 50 ? { size: String(s) } : {}) })}`, run);
  const trustText = ctx.g5_passed_weights_sha256 && ctx.g5_passed_weights_sha256 === ctx.weights_sha256 ? "validated" : "unvalidated — detector not yet validated";
  const pagerProps = { page, pages, total: rows.length, size, hrefFor: listHref, noun: "nodes", hidden: { run } };

  return (
    <div data-testid="market-page" data-condition={condition}>
      <a href="#graph" className="skip-link">skip to bubble map</a>
      <RunBar run={run} ctx={ctx} active="markets" requested={sp?.run} />
      <a href={href("/markets", run)} className="text-2xs underline">← markets</a>
      <h1 className="mt-1 text-xl font-semibold">{m.question}</h1>
      <p className="text-2xs text-slate-600">event: {m.event_title ?? m.event_id ?? "—"}</p>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="card text-2xs" data-testid="market-header">
          <div>outcome: {m.void ? <b data-void="1">void (no winner)</b> : <b>winner: {outcomeName(String(m.winner_index))}</b>}</div>
          <div>resolution (chain ConditionResolution): {fmtT(m.resolution_ts_unix, true)} · tx <span className="font-mono">{m.resolution_tx?.slice(0, 12)}…</span></div>
          <div title="step-4 side-car t_ref = last taker fill before resolution">T_ref (position rebuild instant): {fmtT(m.t_ref_unix, true)} · source: chain step-4 side-car (last taker fill before resolution)</div>
          <div>Gamma closedTime − chain resolution: {m.closed_at_delta_s == null ? "—" : `${m.closed_at_delta_s} s`} (cross-check only) · neg_risk {String(m.neg_risk)}</div>
        </div>
        <div className="card text-2xs" data-testid="market-volume">
          <div>volume: <b>{num(v.volume_taker_shares, 0)} shares</b> · <b>{num(v.volume_taker_usdc, 0)} USDC-eq.</b> (walk taker, {num(v.taker_rows, 0)} API rows, not deduplicated)</div>
          <div className="text-slate-500">Gamma reports: {num(v.gamma_volume_shares_crosscheck, 0)} shares (cross-check)</div>
          <div>post-resolution fills: {num(v.post_resolution_taker_fills, 0)} (walk taker), excluded from signals</div>
          <div className="mt-1">nodes: <b>{v.n_nodes}</b> wallets with net shares &gt; 0 at T_ref (rebuilt from trades, walk all)
            <span className="text-slate-500"> · API holders count: {v.api_holders_count_crosscheck} (net per user, post-redemption; cross-check only)</span></div>
        </div>
      </div>

      <section id="graph" className="card mt-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-slate-700" data-testid="graph-legend">
          <b className="text-sm">Bubble map</b>
          <span data-testid="graph-count">showing {v.n_graph_nodes} of {v.n_nodes} nodes (top by anomalous pattern score at this market; scored wallets only)</span>
          <span>size = net shares at T_ref</span>
          <span className="inline-flex items-center gap-1">colour = score <span className="inline-block h-2 w-16 rounded" style={{ background: `linear-gradient(90deg, ${scoreColour(0)}, ${scoreColour(0.5)}, ${scoreColour(1)})` }} aria-hidden="true" /> 0 → 1</span>
          <span>NA = hatched grey (not the scale minimum)</span>
          <span>edges: dashed = co-timing (S7), {v.edges.length} shown of {v.n_s7_pairs_total}; {v.s6_edges_note}</span>
          <TrustIcon ctx={ctx} />
        </div>
        <div className="mt-2"><BubbleGraph nodes={graphNodes} edges={v.edges} walletHref={href("/wallet/", run)} trustText={trustText} /></div>
      </section>

      <section className="card mt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="card-title">All nodes (complete across pages)</h2>
          <a className="pager-btn" href={href(`/api/market-nodes/${condition}`, run)} data-testid="nodes-csv" download>download all {v.n_nodes} nodes (CSV)</a>
        </div>
        <div className="mt-2"><Pager id="nodes-top" {...pagerProps} /></div>
        <div className="scroll-x mt-2">
          <table className="w-full min-w-[720px] text-2xs" data-testid="node-table">
            <thead className="border-b"><tr><th scope="col" className="th">wallet</th><th scope="col" className="th text-right">net shares at T_ref</th><th scope="col" className="th">outcome(s) held</th>
              <th scope="col" className="th">score (this market)</th><th scope="col" className="th">prefilter</th><th scope="col" className="th">in graph</th></tr></thead>
            <tbody>
              {shown.map((n) => (
                <tr key={n.wallet} className="border-b border-slate-100" data-testid="node-row" data-wallet={n.wallet} data-shares={n.shares} data-in-graph={graphSet.has(n.wallet) ? "1" : "0"}>
                  <td className="td"><CopyAddress address={n.wallet} walletHref={hasWalletPage(run, n.wallet) ? href(`/wallet/${n.wallet}`, run) : null} /></td>
                  <td className="td text-right tabular-nums">{n.shares > 0 && n.shares < 0.01 ? "<0.01" : num(n.shares, 2)}</td>
                  <td className="td">{Object.entries(n.shares_by_outcome).map(([oi, s]) => `${outcomeName(oi)} ${s > 0 && s < 0.01 ? "<0.01" : num(s, s < 1 ? 2 : 0)}`).join(" · ")}</td>
                  <td className="td">
                    {n.score == null
                      ? <NaBadge reason="not_profiled" ctx={ctx} full />
                      : <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: scoreColour(n.score) }} aria-hidden="true" />{n.score.toFixed(2)} <TrustIcon ctx={ctx} />{n.s6_incomplete ? <S6IncompleteMarker /> : null}</span>}
                  </td>
                  <td className="td">{n.prefilter_pass == null ? "—" : <PrefilterMarker pass={n.prefilter_pass} />}</td>
                  <td className="td">{graphSet.has(n.wallet) ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2"><Pager id="nodes-bottom" {...pagerProps} /></div>
      </section>
      <SourceFooter ctx={ctx} />
    </div>
  );
}
