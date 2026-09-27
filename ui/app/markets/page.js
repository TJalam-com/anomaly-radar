// V2 market picker (G6 M1, T-08): title, event, outcome, chain resolution date/instant + source; sortable columns.
import { context, href, markets, resolveRun } from "@/lib/data";
import { fmtD, fmtT } from "@/lib/format";
import { RunBar, SourceFooter } from "../components/ui";

export const dynamic = "force-dynamic";

export default async function Markets({ searchParams }) {
  const sp = await searchParams;
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const mk = markets(run);
  const list = Object.entries(mk).sort((a, b) => (a[1].resolution_ts_unix ?? 0) - (b[1].resolution_ts_unix ?? 0) || a[1].question.localeCompare(b[1].question));
  return (
    <div>
      <RunBar run={run} ctx={ctx} active="markets" requested={sp?.run} />
      <h1 className="text-xl font-semibold">Markets <span className="font-normal text-slate-500">({list.length}, primary set M)</span></h1>
      <p className="mt-1 text-2xs text-slate-600">Resolution = on-chain ConditionResolution time (step-4 side-car); Gamma closedTime only as a cross-check. Several markets share a question text; the event and resolution date tell them apart.</p>
      <div className="card scroll-x mt-2 p-0">
        <table className="w-full min-w-[760px] text-2xs" data-testid="market-list">
          <thead className="border-b bg-slate-50"><tr>
            <th scope="col" className="th">market</th><th scope="col" className="th">event</th><th scope="col" className="th">resolved (date)</th>
            <th scope="col" className="th">outcome</th><th scope="col" className="th">chain resolution (UTC)</th><th scope="col" className="th">closedTime − resolution</th></tr></thead>
          <tbody>
            {list.map(([c, m]) => (
              <tr key={c} className="border-b border-slate-100" data-condition={c}>
                <td className="td"><a className="underline-offset-2 hover:underline" href={href(`/market/${c}`, run)}>{m.question}</a></td>
                <td className="td text-slate-600">{m.event_title ?? m.event_id ?? "—"}</td>
                <td className="td whitespace-nowrap tabular-nums">{fmtD(m.resolution_ts_unix)}</td>
                <td className="td">{m.void ? "void (no winner)" : `winner: ${m.outcomes?.[String(m.winner_index)] ?? "—"}`}</td>
                <td className="td whitespace-nowrap">{fmtT(m.resolution_ts_unix, true)}</td>
                <td className="td tabular-nums">{m.closed_at_delta_s == null ? "—" : `${m.closed_at_delta_s} s (cross-check)`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <SourceFooter ctx={ctx} />
    </div>
  );
}
