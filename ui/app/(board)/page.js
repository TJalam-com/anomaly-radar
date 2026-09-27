// V1 Radar leaderboard (G6 r6 §1 V1, C1; design pass D1–D5; F3–F7; T-09/T-10/T-11; UX1–UX3).
// Pipeline totals only (U6); NA never 0 (U4); trust label on every score (U5, compact icon with accessible name).
import { context, href, leaderboard, resolveRun } from "@/lib/data";
import { breakdown, drivers, fmtWeight } from "@/lib/contrib";
import { intParam, num, PAGE_SIZES } from "@/lib/format";
import { naText } from "@/lib/na";
import { SIGNAL_NAMES, SIGS } from "@/lib/signals";
import CopyAddress from "../components/CopyAddress";
import Pager from "../components/Pager";
import { NaBadge, PendingChip, PrefilterMarker, RunBar, S6IncompleteMarker, ScoreBar, SourceFooter, SummaryBar, TrustIcon } from "../components/ui";

export const dynamic = "force-dynamic";
const SORTS = { score: "anomalous pattern score", stake: "low-odds stake", markets: "markets hit" };

export default async function Leaderboard({ searchParams }) {
  const sp = await searchParams;
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const lb = leaderboard(run, "all");
  const W = ctx.weights_applied || {};
  const notices = [];

  // run-wide pending signals (NA for every ranked wallet) -> one chip (D2); the rest get columns
  const pending = SIGS.filter((k) => lb.rows.length && lb.rows.every((r) => r.signals[k].na_reason)).map((k) => {
    const counts = {};
    for (const r of lb.rows) counts[r.signals[k].na_reason] = (counts[r.signals[k].na_reason] || 0) + 1;
    return { sig: k, reason: Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0] };
  });
  const pendingSet = new Set(pending.map((p) => p.sig));
  const visible = SIGS.filter((k) => !pendingSet.has(k));

  // params (F7: invalid values ignored with a notice; out-of-range pages clamped with a notice)
  let minScore = 0;
  if (sp?.min != null && sp.min !== "") {
    const m = Number(sp.min);
    if (Number.isFinite(m) && m >= 0 && m <= 1) minScore = m; else notices.push(`invalid filter value ignored: min=${String(sp.min).slice(0, 12)}`);
  }
  let needSig = null;
  if (sp?.sig) {
    if (!SIGS.includes(sp.sig)) notices.push(`invalid filter value ignored: sig=${String(sp.sig).slice(0, 12)}`);
    else if (pendingSet.has(sp.sig)) notices.push(`${sp.sig} ${SIGNAL_NAMES[sp.sig]} has no data for any wallet in this run (filter ignored)`);
    else needSig = sp.sig;
  }
  if (sp?.scope != null && sp.scope !== "all") notices.push(`unknown scope “${String(sp.scope).slice(0, 24)}” — showing all (the leaderboard ranks scope all only)`);
  const sort = SORTS[sp?.sort] ? sp.sort : "score";
  if (sp?.sort && !SORTS[sp.sort]) notices.push(`invalid sort ignored: ${String(sp.sort).slice(0, 12)}`);
  const sizeP = intParam(sp?.size, { def: 50, min: 1, max: 1000 });
  const size = PAGE_SIZES.includes(sizeP.value) ? sizeP.value : 50;
  if (sp?.size && !PAGE_SIZES.includes(Number(sp.size))) notices.push(`invalid page size ignored: ${String(sp.size).slice(0, 12)} (use 25, 50 or 100)`);

  let rows = lb.rows.filter((r) => r.score >= minScore && (!needSig || !r.signals[needSig].na_reason));
  const key = { score: (r) => r.score, stake: (r) => r.s3_low_odds_stake_usdc ?? -1, markets: (r) => r.markets_hit }[sort];
  rows = [...rows].sort((a, b) => key(b) - key(a) || a.wallet.localeCompare(b.wallet));
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const pageP = intParam(sp?.page, { def: 1, min: 1, max: pages });
  if (pageP.invalid) notices.push(`invalid page ignored: ${String(sp.page).slice(0, 12)}, showing page 1`);
  if (pageP.clamped) notices.push(Number(sp.page) > pages ? `page out of range, showing last page (${pages})` : "page out of range, showing page 1");
  const page = pageP.value;
  const shown = rows.slice((page - 1) * size, page * size);
  const filtered = minScore > 0 || needSig != null;
  const base = { ...(minScore ? { min: String(minScore) } : {}), ...(needSig ? { sig: needSig } : {}), ...(sort !== "score" ? { sort } : {}) };
  const listHref = (p, s) => href(`/?${new URLSearchParams({ ...base, ...(s !== 50 ? { size: String(s) } : {}), ...(p > 1 ? { page: String(p) } : {}) })}`, run);
  const back = listHref(page, size);
  const sortHref = (k) => {
    const o = { ...base };
    delete o.sort;
    if (k !== "score") o.sort = k;
    if (size !== 50) o.size = String(size);
    return href(`/?${new URLSearchParams(o)}`, run);
  };
  const unknown = [...new Set(lb.rows.flatMap((r) => SIGS.map((k) => r.signals[k].na_reason)).filter((x) => x && !naText(x, ctx).known))];
  const plants = ctx.fixture ? (ctx.plants || {}) : {};
  const pagerProps = { page, pages, total: rows.length, size, hrefFor: listHref, noun: "wallets", hidden: { run, ...base } };

  return (
    <div>
      <a href="#pager-top" className="skip-link">skip to pager and table</a>
      <RunBar run={run} ctx={ctx} active="leaderboard" requested={sp?.run} />
      {plants.dom_tooltip && <span data-plant="C0-1" title={plants.dom_tooltip} className="text-2xs text-slate-400">ⓘ</span>}
      <SummaryBar ctx={ctx} items={[["ranked wallets", `${lb.n_ranked} of ${lb.n_universe.toLocaleString("en-US")} in U(all)`], ["scope", "all — primary markets (M, 171)"],
        ["source", <a key="s" href={href("/about#sources", run)} className="underline">SNAP-003 + chain resolutions</a>]]} />

      <div className="flex flex-wrap items-end justify-between gap-2 sm:gap-3">
        <div>
          <h1 className="text-xl font-semibold">Leaderboard <span className="font-normal text-slate-500">— anomalous pattern score</span></h1>
          <p className="text-2xs text-slate-600">Scores are probabilistic indicators, not proof. <a className="underline" href={href("/about", run)}>What is this score?</a></p>
        </div>
        <form className="flex flex-wrap items-end gap-2 text-2xs" method="get" data-testid="filter-form">
          <input type="hidden" name="run" value={run} />
          {sort !== "score" && <input type="hidden" name="sort" value={sort} />}
          <label className="flex flex-col text-slate-600">min score
            <input name="min" type="number" step="0.01" min="0" max="1" defaultValue={minScore || ""} className="w-20 rounded border border-slate-300 px-1 py-1" />
          </label>
          <label className="flex flex-col text-slate-600">signal has data
            <select name="sig" defaultValue={needSig || ""} className="rounded border border-slate-300 px-1 py-1">
              <option value="">any</option>
              {SIGS.map((k) => <option key={k} value={k} disabled={pendingSet.has(k)}>{k} {SIGNAL_NAMES[k]}{pendingSet.has(k) ? " (NA in this run)" : ""}</option>)}
            </select>
          </label>
          <button className="rounded bg-slate-800 px-3 py-1.5 text-white" type="submit">apply</button>
          {filtered && <a className="pager-btn" href={href("/", run)}>clear</a>}
          {plants.share_button && <button type="button" data-plant="C0-8" className="pager-btn">share</button>}
        </form>
      </div>

      {notices.map((n, i) => <p key={i} data-testid="param-notice" className="mt-2 rounded border border-slate-300 bg-white px-3 py-1 text-2xs text-slate-700">{n}</p>)}
      {unknown.length > 0 && (
        <div data-testid="ui-health-flag" className="mt-2 rounded border border-rose-300 bg-rose-50 px-3 py-2 text-2xs text-rose-800">
          UI health: NA reason(s) without a plain-text rendering in G6 §2a, shown raw in brackets: {unknown.join(", ")}
        </div>
      )}

      <div className="mt-2 sm:mt-3"><PendingChip pending={pending} ctx={ctx} /></div>

      <p className="text-2xs text-slate-600" data-testid="ranked-count">
        {filtered ? `${rows.length} of ${lb.n_ranked} ranked wallets match the filter` : `${lb.n_ranked} ranked wallets`} · sorted by {SORTS[sort]} (high to low); ties ordered by wallet address.
        Scores are shown to 2 decimals, so equal-looking scores can differ below 0.005 (tiny float differences, e.g. 1e-17, also exist).
      </p>

      <div id="pager-top" className="mt-2"><Pager id="top" {...pagerProps} /></div>

      <div className="card scroll-x mt-2 p-0">
        <table className="w-full min-w-[760px] text-sm" data-testid="leaderboard">
          <caption className="sr-only">ranked wallets by anomalous pattern score</caption>
          <thead className="border-b border-slate-200 bg-slate-50">
            <tr>
              <th scope="col" className="th w-12">rank</th>
              <th scope="col" className="th">wallet</th>
              <th scope="col" className="th" aria-sort={sort === "score" ? "descending" : "none"}><a href={sortHref("score")} className="underline-offset-2 hover:underline">anomalous pattern score{sort === "score" ? " ▾" : ""}</a></th>
              <th scope="col" className="th" title="the two signals with the largest weight × component; if the second place is tied, all tied signals are listed as “tied: …”; values in the score breakdown">driven by</th>
              {/* A10: one column per signal from xl (1280) up; below xl one compact "signals" cell so the table fits at >= 900 px */}
              {visible.map((k) => <th key={k} scope="col" className="th hidden xl:table-cell" title={`${k}: ${SIGNAL_NAMES[k]}`}>{k}<br /><span className="font-normal">{SIGNAL_NAMES[k]}</span></th>)}
              <th scope="col" className="th xl:hidden" title={visible.map((k) => `${k} ${SIGNAL_NAMES[k]}`).join(" · ")}>signals<br /><span className="font-normal">component per signal</span></th>
              <th scope="col" className="th text-right" aria-sort={sort === "stake" ? "descending" : "none"}><a href={sortHref("stake")} className="hover:underline">low-odds stake{sort === "stake" ? " ▾" : ""}</a><br /><span className="font-normal">USDC-eq., walk all</span></th>
              <th scope="col" className="th text-right" aria-sort={sort === "markets" ? "descending" : "none"}><a href={sortHref("markets")} className="hover:underline">markets hit{sort === "markets" ? " ▾" : ""}</a><br /><span className="font-normal">pre-resolution legs</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => {
              const bd = breakdown(r.signals, W, r.score);
              const drv = drivers(bd.parts);
              return (
                <tr key={r.wallet} className="border-b border-slate-100 align-top hover:bg-slate-50" data-testid="lb-row" data-wallet={r.wallet} data-rank={r.rank}
                  {...(r.scope_origin ? { "data-scope-origin": r.scope_origin } : {})}
                  {...(i === 0 ? { elementtiming: "lb-first-row" } : {})}>
                  <td className="td tabular-nums text-slate-600">{r.rank}</td>
                  <td className="td">
                    <CopyAddress address={r.wallet} walletHref={href(`/wallet/${r.wallet}?${new URLSearchParams({ back })}`, run)} />
                    <div className="mt-0.5"><PrefilterMarker pass={r.prefilter_pass} /></div>
                  </td>
                  <td className="td" data-score={r.score}>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5"><span className="text-lg font-semibold tabular-nums">{r.score.toFixed(2)}</span><ScoreBar score={r.score} /><TrustIcon ctx={ctx} /></div>
                    <div className="text-2xs text-slate-500">{r.n_signals_na} of 8 signals NA</div>
                    <details className="text-2xs" data-testid="score-breakdown">
                      <summary className="cursor-pointer text-slate-600">how it adds up</summary>
                      <ul className="mt-1 space-y-0.5 tabular-nums">
                        {bd.parts.filter((d) => d.weight > 0).map((d) => (
                          <li key={d.sig} data-contrib={d.sig} data-product={d.value}>{d.sig} {SIGNAL_NAMES[d.sig]}: {fmtWeight(d.weight)} × {d.na ? "NA (adds 0)" : d.component.toFixed(4)} = <b>{d.value.toFixed(4)}</b></li>
                        ))}
                        <li className="border-t pt-0.5">sum {bd.sum.toFixed(4)} · stored total <b>{r.score.toFixed(4)}</b></li>
                        {bd.mismatch && <li data-testid="breakdown-mismatch" className="font-semibold text-rose-700">mismatch: products do not sum to the stored total</li>}
                        <li className="text-slate-500">signals with weight 0 have no data for any wallet in this run</li>
                      </ul>
                    </details>
                  </td>
                  <td className="td text-2xs text-slate-700" data-testid="drivers">
                    {drv.top.length + drv.tied.length === 0 ? "—" : <>
                      {drv.top.flatMap((d, j) => [j ? " " : null, <span key={d.sig} className="whitespace-nowrap">{j ? "· " : ""}{SIGNAL_NAMES[d.sig]}</span>])}
                      {drv.top.length > 0 && drv.tied.length > 0 ? " " : null}
                      {drv.tied.length > 0 && <span data-testid="drivers-tied" className="whitespace-nowrap" title={`tied: ${drv.tied.map((d) => `${d.sig} ${SIGNAL_NAMES[d.sig]}`).join(" · ")}`}>
                        {drv.top.length ? "· " : ""}tied{drv.tied.length > 2 ? ` (${drv.tied.length})` : ""}: </span>}
                      {/* short names (QA N1); A11 compact: at most 2 named, the rest in a "+N" chip (tooltip + screen-reader text) */}
                      {drv.tied.slice(0, 2).flatMap((d, j) => [j ? " " : null, <span key={d.sig} className="whitespace-nowrap" data-tied={d.sig}>{j ? "· " : ""}{SIGNAL_NAMES[d.sig]}</span>])}
                      {drv.tied.length > 2 && <> <span className="whitespace-nowrap cursor-help text-slate-500" data-testid="drivers-tied-more"
                        title={drv.tied.slice(2).map((d) => SIGNAL_NAMES[d.sig]).join(" · ")}>· +{drv.tied.length - 2}<span className="sr-only"> more tied: {drv.tied.slice(2).map((d) => SIGNAL_NAMES[d.sig]).join(", ")}</span></span></>}
                    </>}
                  </td>
                  {visible.map((k) => {
                    const s = r.signals[k];
                    return (
                      <td key={k} className="td hidden xl:table-cell" data-signal={k}>
                        {s.na_reason ? <NaBadge reason={s.na_reason} ctx={ctx} />
                          : <span className="tabular-nums text-2xs" data-component={s.component}>{s.component?.toFixed(2)}{k === "S6" && s.incomplete_path && s.raw > 0 && <S6IncompleteMarker compact />}</span>}
                      </td>
                    );
                  })}
                  <td className="td xl:hidden" data-testid="signals-compact">
                    <div className="grid grid-cols-[repeat(4,max-content)] gap-x-2 gap-y-0.5 text-2xs tabular-nums">
                      {visible.map((k) => {
                        const s = r.signals[k];
                        return (
                          <span key={k} className="whitespace-nowrap" data-signal-compact={k} title={`${k} ${SIGNAL_NAMES[k]}`}>
                            <span className="text-slate-500">{k}</span>{" "}
                            {s.na_reason ? <NaBadge reason={s.na_reason} ctx={ctx} /> : <>{s.component?.toFixed(2)}{k === "S6" && s.incomplete_path && s.raw > 0 && <S6IncompleteMarker compact />}</>}
                          </span>
                        );
                      })}
                    </div>
                  </td>
                  <td className="td text-right tabular-nums">{r.signals.S3.na_reason ? <NaBadge reason={r.signals.S3.na_reason} ctx={ctx} /> : num(r.s3_low_odds_stake_usdc, 0)}</td>
                  <td className="td text-right tabular-nums">{r.markets_hit}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-2"><Pager id="bottom" {...pagerProps} /></div>
      {!plants.scope_limit_removed && <p data-testid="scope-limit" className="mt-3 text-2xs text-slate-600">Scope limit: {ctx.scope_limit_text}. Prefilter rule: {ctx.prefilter_rule}.</p>}
      {!plants.footer_removed && <SourceFooter ctx={ctx} extra={<span title={ctx.export_manifest["leaderboard_all.json"]}><span className="text-slate-400">view:</span> leaderboard_all {ctx.export_manifest["leaderboard_all.json"].slice(0, 8)}…</span>} />}
      {plants.lazy_chunk && <script async src={`/plant/lazy?run=${encodeURIComponent(run)}`} />}
    </div>
  );
}
