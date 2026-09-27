// "What is this score?" (D4, UX2, UX3, T-04): plain-language explainer, the actual weights applied in this run,
// NA != 0, and data sources. G6 U1: "anomalous pattern score"; no claim about any person.
import { context, href, resolveRun } from "@/lib/data";
import { num } from "@/lib/format";
import { NA_EXPLAINER, SIGNAL_EXPLAIN, SIGNAL_NAMES, SIGS } from "@/lib/signals";
import { RunBar, SourceFooter, TrustIcon } from "../components/ui";

export const dynamic = "force-dynamic";

export default async function About({ searchParams }) {
  const sp = await searchParams;
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const W = ctx.weights_applied || {};
  const dev = /DEV|not-frozen/i.test(ctx.weights_version || "");
  return (
    <div className="max-w-3xl">
      <RunBar run={run} ctx={ctx} active="about" requested={sp?.run} />
      <h1 className="text-xl font-semibold">What is this score?</h1>
      <div className="card mt-3 space-y-2 text-sm">
        <p>The <b>anomalous pattern score</b> (0 to 1) combines eight signals computed from public trades and public on-chain data.
          A high score means the wallet&apos;s trading shows several unusual patterns at once. It is a probabilistic indicator, <b>not proof</b> of
          anything, and it makes no claim about who controls a wallet.</p>
        <p>Every score currently carries the trust label <TrustIcon ctx={ctx} />: the detector has not yet passed its validation gate.</p>
        <p data-testid="na-explainer"><b>{NA_EXPLAINER}</b></p>
        <p id="s6-review" data-testid="s6-under-review">S6 (linked wallets) is under review: in runs with wallet profiles, many links pass through widely shared addresses
          (likely exchanges, relays or deposit services) rather than a private shared funder, so a single link is weak evidence and wallet clusters are not formed yet.</p>
      </div>

      <h2 className="mt-5 text-base font-semibold" id="weights">The eight signals and the weights used in this run</h2>
      <p className="text-2xs text-slate-600">
        score = Σ weight × component, over signals that have data (NA adds nothing). Weights file: <b>{ctx.weights_version || "unknown"}</b>{" "}
        (sha {ctx.weights_file_sha256?.slice(0, 8)}…). {dev ? "These are development weights (equal over the signals with data in this run), NOT the frozen W0 weights; frozen weights come from the pre-registered weight search." : ""}
      </p>
      <div className="card scroll-x mt-2 p-0">
        <table className="w-full min-w-[560px] text-sm" data-testid="weights-table">
          <thead className="border-b bg-slate-50"><tr><th scope="col" className="th">signal</th><th scope="col" className="th">what it measures</th><th scope="col" className="th text-right">weight applied</th></tr></thead>
          <tbody>
            {SIGS.map((k) => (
              <tr key={k} className="border-b border-slate-100" data-weight={k}>
                <td className="td whitespace-nowrap"><b>{k}</b> {SIGNAL_NAMES[k]}</td>
                <td className="td text-2xs text-slate-700">{SIGNAL_EXPLAIN[k]}</td>
                <td className="td text-right tabular-nums">{num(W[k], 4)}{W[k] === 0 ? <span className="block text-2xs text-slate-500">no data for any wallet in this run</span> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="mt-5 text-base font-semibold" id="sources">Data sources</h2>
      <ul className="card mt-2 list-disc space-y-1 pl-6 text-2xs text-slate-700">
        <li>Trades: public Polymarket data API (taker and maker legs), snapshot SNAP-003 (manifest hash in every page footer).</li>
        <li>Market resolution: the on-chain ConditionResolution event (Polygon), not the API close time.</li>
        <li>Wallet profiles (funding, redemptions, lifetime volume): {ctx.profile_label}.</li>
        <li>Public event times (S4): {ctx.event_times_sha256 ? `file ${ctx.event_times_sha256.slice(0, 8)}…` : "not in this run (S4 is NA)"}.</li>
        <li>Read-only: this site never trades, bets or contacts anyone.</li>
      </ul>
      <p className="mt-4 text-2xs"><a className="underline" href={href("/", run)}>← leaderboard</a></p>
      <SourceFooter ctx={ctx} />
    </div>
  );
}
