// V3 Wallet page (G6 r6 §1 V3, C3, W1–W7; design D6; F8; T-02/T-05/T-06/T-13; UX4).
import { notFound } from "next/navigation";
import { context, hasWalletPage, href, isAddress, markets, prices, resolveRun, sharedCounterparties, wallet, walletLinks } from "@/lib/data";
import { fmtT, intParam, num, PAGE_SIZES } from "@/lib/format";
import { naText } from "@/lib/na";
import { SIGNAL_EXPLAIN, SIGNAL_NAMES, SIGS } from "@/lib/signals";
import CopyAddress from "../../components/CopyAddress";
import Pager from "../../components/Pager";
import { NaBadge, PrefilterMarker, RunBar, S6IncompleteMarker, S6UnderReview, ScoreBar, SourceFooter, TrustIcon } from "../../components/ui";

export const dynamic = "force-dynamic";
const PROFILED_ONLY = ["S1", "S2", "S6", "S8"];

function scopeInfo(sc, mk) {
  if (sc === "all") return { label: "all — primary markets (M)", market: null };
  if (sc.startsWith("event:")) {
    const id = sc.slice(6);
    const title = Object.values(mk).find((m) => String(m.event_id) === id)?.event_title;
    return { label: `event: ${title ?? id}`, market: null };
  }
  return { label: mk[sc]?.question ?? `${sc.slice(0, 10)}…`, market: sc };
}

function SignalValue({ sig, s, sd, ctx }) {
  if (s.na_reason) return <NaBadge reason={s.na_reason} ctx={ctx} full />;
  const ev = s.evidence || {};
  switch (sig) {
    case "S1": return <span>{s.raw == null || s.raw === Infinity ? "no qualifying bet" : `${num(s.raw, 1)} h from first activity/funding to first large bet`}</span>;
    case "S2": return <span>{num(s.raw, 3)} of lifetime volume (both sides)</span>;
    case "S3": return <span>{num(s.raw, 0)} USDC-eq. staked below price 0.20 (walk all, before resolution)</span>;
    case "S4": return <span>{num(s.raw, 3)} of stake on the winner inside the pre-event window</span>;
    case "S5": return <span data-s5-p={ev.pval}>p-value {ev.pval != null ? Number(ev.pval).toExponential(2) : "—"} (−log10 p = {num(s.raw, 2)}); {ev.k} of {ev.n} resolved bets won; mean entry price {num(sd.s5_mean_entry, 3)}</span>;
    case "S6": return <span>{num(s.raw, 0)} linked profiled wallets{(s.incomplete_path || ev.incomplete_path || ev.edges_possibly_incomplete) && s.raw > 0 ? <S6IncompleteMarker /> : null}</span>;
    case "S7": return <span>{num(s.raw, 0)} other wallets bought the same outcome within the window</span>;
    case "S8": return <span>{num(s.raw, 2)} (exit behaviour after resolution)</span>;
    default: return <span>{num(s.raw)}</span>;
  }
}

function Lane({ cond, mk, fills, pr, marketHref, hideLabel }) {
  const m = mk[cond] || {};
  const byOutcome = {};
  for (const f of fills) byOutcome[f[2]] = (byOutcome[f[2]] || 0) + f[4];
  const oi = String(Object.entries(byOutcome).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "0");
  const series = pr?.series?.[oi] || [];
  const t0 = Math.min(fills[0][0], series[0]?.[0] ?? fills[0][0]);
  const t1 = Math.max(m.resolution_ts_unix ?? 0, fills[fills.length - 1][0]) + 3600;
  const W = 900, H = 80, pad = 6;
  const x = (t) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const y = (p) => pad + (1 - p) * (H - 2 * pad);
  const inSpan = series.filter((s) => s[0] >= t0 && s[0] <= t1);
  const step = Math.max(1, Math.ceil(inSpan.length / 400));
  const pts = inSpan.filter((_, i) => i % step === 0 || i === inSpan.length - 1).map((s) => `${x(s[0]).toFixed(1)},${y(s[1]).toFixed(1)}`).join(" ");
  const binS = Math.max(3600, Math.ceil((t1 - t0) / 120 / 3600) * 3600);
  const bins = {};
  for (const f of fills) {
    const k = `${Math.floor(f[0] / binS)}|${f[3]}|${f[6]}|${f[2]}`;
    const b = (bins[k] ||= { t: f[0], buy: f[3], post: f[6], oi: f[2], size: 0, n: 0, pw: 0 });
    b.size += f[4]; b.n += 1; b.pw += f[4] * f[5];
  }
  const marks = Object.values(bins);
  const maxS = Math.max(...marks.map((b) => b.size), 1);
  return (
    <div className="mt-3" data-testid="lane" data-condition={cond}>
      <div className="text-2xs text-slate-700">
        <a className="font-medium underline-offset-2 hover:underline" href={marketHref}>{m.question ?? cond}</a>{" · "}
        {m.void ? <span>void (no winner)</span> : <span className="text-slate-500">winner: {m.outcomes?.[String(m.winner_index)] ?? "—"}</span>}
        {!hideLabel && <>{" · "}price line: outcome “{m.outcomes?.[oi] ?? oi}”, {pr?.source ?? "no price data"}, {pr?.resolution ?? ""}</>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-20 w-full rounded border border-slate-200 bg-white" role="img" aria-label={`bets and price for ${m.question ?? cond}`}>
        <polyline points={pts} fill="none" stroke="#94a3b8" strokeWidth="1.2" />
        {m.resolution_ts_unix && <line x1={x(m.resolution_ts_unix)} x2={x(m.resolution_ts_unix)} y1="0" y2={H} stroke="#0f172a" strokeDasharray="3 2" />}
        {marks.map((b, i) => (
          <circle key={i} cx={x(b.t)} cy={y(b.pw / b.size)} r={2 + 6 * Math.sqrt(b.size / maxS)}
            fill={b.post ? "#cbd5e1" : b.buy ? "#334155" : "#94a3b8"} fillOpacity={b.oi === Number(oi) ? 0.8 : 0.35}>
            <title>{`${b.buy ? "BUY" : "SELL"} ${num(b.size, 2)} shares @ ${num(b.pw / b.size, 3)} (${b.n} ${b.n === 1 ? "fill" : "fills"}, bin starting ${fmtT(b.t)})${b.post ? ", after resolution, excluded from signals" : ""}`}</title>
          </circle>
        ))}
      </svg>
      <div className="text-2xs text-slate-500">
        {fmtT(t0)} → {fmtT(t1)} · dashed = chain resolution {fmtT(m.resolution_ts_unix)} · dark = BUY, mid-grey = SELL, light = after resolution (excluded) · size ∝ shares (bins of {binS / 3600} h) · no public event time in this run (S4 NA)
      </div>
    </div>
  );
}

function LanePager({ lpage, lpages, total, lsize, q }) {
  const from = total ? (lpage - 1) * lsize + 1 : 0, to = Math.min(total, lpage * lsize);
  return (
    <nav aria-label="market lanes pages" className="flex flex-wrap items-center gap-1 text-xs" data-testid="pager-lanes">
      {lpage > 1 ? <a className="pager-btn" href={q({ lpage: lpage - 1 })} aria-label="previous markets">‹ prev</a> : <span className="pager-btn opacity-40">‹ prev</span>}
      <span className="px-1 text-slate-600">markets {from}–{to} of {total}</span>
      {lpage < lpages ? <a className="pager-btn" href={q({ lpage: lpage + 1 })} aria-label="next markets">next ›</a> : <span className="pager-btn opacity-40">next ›</span>}
      <span className="ml-2 text-slate-500">per page</span>
      {[6, 12, 24].map((n) => n === lsize ? <span key={n} className="pager-btn pager-current" aria-current="true">{n}</span>
        : <a key={n} className="pager-btn" href={q({ lsize: n, lpage: 1 })}>{n}</a>)}
    </nav>
  );
}

export default async function WalletPage({ params, searchParams }) {
  const { address } = await params;
  const sp = await searchParams;
  if (!isAddress(String(address).toLowerCase())) notFound();
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const w = wallet(run, address);
  if (!w) notFound();
  const mk = markets(run);
  const scopes = Object.keys(w.scopes).sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : scopeInfo(a, mk).label.localeCompare(scopeInfo(b, mk).label)));
  const scope = scopes.includes(sp?.scope) ? sp.scope : "all";
  const unknownScope = typeof sp?.scope === "string" && sp.scope !== "" && !scopes.includes(sp.scope);
  const sd = w.scopes[scope];
  const all = w.scopes.all || sd;
  const below = !sd.prefilter_pass;
  const back = typeof sp?.back === "string" && sp.back.startsWith("/") && !sp.back.startsWith("//") ? sp.back : href("/", run);

  const ssize = PAGE_SIZES.includes(Number(sp?.ssize)) ? Number(sp.ssize) : 25;
  const spages = Math.max(1, Math.ceil(scopes.length / ssize));
  const spage = intParam(sp?.spage, { def: 1, min: 1, max: spages }).value;
  const fills = w.fills;
  const bsize = PAGE_SIZES.includes(Number(sp?.bsize)) ? Number(sp.bsize) : 50;
  const bpages = Math.max(1, Math.ceil(fills.length / bsize));
  const bpage = intParam(sp?.bpage, { def: 1, min: 1, max: bpages }).value;
  const byCond = {};
  for (const f of fills) (byCond[f[1]] ||= []).push(f);
  const stakeOf = (c) => byCond[c].reduce((a, f) => a + f[4] * f[5], 0);
  const allLanes = Object.keys(byCond).sort((a, b) => stakeOf(b) - stakeOf(a) || a.localeCompare(b));
  const lsize = [6, 12, 24].includes(Number(sp?.lsize)) ? Number(sp.lsize) : 6;     // lane pager (T-06 page weight)
  const lpages = Math.max(1, Math.ceil(allLanes.length / lsize));
  const lpage = intParam(sp?.lpage, { def: 1, min: 1, max: lpages }).value;
  const lanes = allLanes.slice((lpage - 1) * lsize, lpage * lsize);
  const nPost = fills.filter((f) => f[6]).length;

  // V4 ego view (QA (b)-lite): this wallet's S6 links, paged; no transitive grouping
  const L = below ? null : walletLinks(run, w.wallet);
  const stopList = ctx.links?.stop_list || [];
  const ksize = PAGE_SIZES.includes(Number(sp?.ksize)) ? Number(sp.ksize) : 25;
  const kpages = Math.max(1, Math.ceil((L?.linked.length || 0) / ksize));
  const kpage = intParam(sp?.kpage, { def: 1, min: 1, max: kpages }).value;
  const kshown = (L?.linked || []).slice((kpage - 1) * ksize, kpage * ksize).map(([o, n, so]) => {
    const other = walletLinks(run, o);
    return { o, n, so, other, shared: sharedCounterparties(L, other, stopList), oneSided: !other?.linked?.some(([x]) => x === w.wallet) };
  });

  const state = { scope, spage, ssize, bpage, bsize, lpage, lsize, kpage, ksize, back };
  const q = (o) => href(`/wallet/${w.wallet}?${new URLSearchParams(Object.fromEntries(Object.entries({ ...state, ...o }).filter(([, v]) => v != null && v !== "")))}`, run);
  const hiddenFor = (skip) => Object.fromEntries(Object.entries({ run, ...state }).filter(([k]) => k !== skip));

  return (
    <div data-testid="wallet-page" data-wallet={w.wallet}>
      <a href="#signals" className="skip-link">skip to signals</a>
      <RunBar run={run} ctx={ctx} active="wallet" requested={sp?.run} />
      {unknownScope && <p data-testid="param-notice" className="mb-2 rounded border border-slate-300 bg-white px-2 py-1 text-2xs text-slate-700">unknown scope “{String(sp.scope).slice(0, 70)}” for this wallet, showing all</p>}
      <a href={back} className="text-2xs underline" data-testid="back-link">← leaderboard</a>
      <h1 className="mt-1 text-xl font-semibold">Wallet</h1>
      <div className="mt-1"><CopyAddress address={w.wallet} full /></div>

      <section className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="summary-cards">
        <div className="card"><div className="muted text-2xs">anomalous pattern score (all)</div>
          <div className="flex items-center gap-2"><span className="text-2xl font-semibold tabular-nums" data-score={all.score}>{all.score == null ? "—" : all.score.toFixed(2)}</span>{all.score != null && <ScoreBar score={all.score} />}</div>
          <div className="text-2xs text-slate-500">{all.n_signals_na} of 8 signals NA</div><TrustIcon ctx={ctx} /></div>
        <div className="card"><div className="muted text-2xs">rank (all)</div><div className="text-2xl font-semibold tabular-nums">{w.rank_all ?? "—"}</div><div className="text-2xs text-slate-500">of {w.n_ranked_all ?? "—"} ranked</div></div>
        <div className="card"><div className="muted text-2xs">markets traded</div><div className="text-2xl font-semibold tabular-nums">{allLanes.length}</div><div className="text-2xs text-slate-500">walk all (taker + maker legs)</div></div>
        <div className="card"><div className="muted text-2xs">low-odds stake (all)</div><div className="text-2xl font-semibold tabular-nums">{all.signals?.S3?.na_reason ? "NA" : num(all.signals?.S3?.raw, 0)}</div><div className="text-2xs text-slate-500">USDC-eq., price &lt; 0.20</div></div>
        <div className="card"><div className="muted text-2xs">prefilter</div><div className="mt-1"><PrefilterMarker pass={all.prefilter_pass} /></div><div className="mt-1 text-2xs text-slate-500">≥ 1000 USDC low-odds buys in M</div></div>
      </section>

      <section id="signals" className="mt-4" data-testid="signal-panel">
        <h2 className="card-title">Signals — {scopeInfo(scope, mk).label}</h2>
        <div className="mt-2 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {SIGS.map((k) => {
            const s = sd.signals[k] || {};
            const forceNa = below && PROFILED_ONLY.includes(k);   // U6/U7: below prefilter -> not profiled, regardless of stored values
            return (
              <div key={k} className="card p-3" data-signal={k}>
                <div className="flex items-baseline justify-between"><b className="text-sm">{k} {SIGNAL_NAMES[k]}</b>
                  <span className="tabular-nums text-sm">{forceNa || s.na_reason ? "NA" : s.component?.toFixed(2)}</span></div>
                <p className="mt-1 text-2xs text-slate-600">{SIGNAL_EXPLAIN[k]}</p>
                <div className="mt-2 text-2xs">{forceNa ? <NaBadge reason="not_profiled" ctx={ctx} full /> : <SignalValue sig={k} s={s} sd={sd} ctx={ctx} />}</div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="mt-4 grid gap-3 md:grid-cols-3">
        {[["First seen", "S1"], ["Funding sources", "S6"]].map(([title, k]) => {
          const s = sd.signals[k] || {};
          const reason = below ? "not_profiled" : s.na_reason;
          return (
            <div key={title} className="card p-3 text-2xs" data-testid={`panel-${title.replace(/ /g, "-").toLowerCase()}`}>
              <h3 className="text-sm font-semibold">{title}</h3>
              <p className="mt-1 text-slate-600">{reason ? <>hidden: {naText(reason, ctx).text}</>
                : ctx.links?.available ? `not exported in this build; the ${k} value above uses it (${ctx.profile_label})` : "this run carries no profile inputs"}</p>
            </div>
          );
        })}
      </section>

      <section className="card mt-4" id="linked" data-testid="linked-wallets">
        <h2 className="card-title">Linked wallets (S6)</h2>
        {ctx.links?.available && <S6UnderReview aboutHref={`${href("/about", run)}#s6-review`} />}
        {below || !L ? (
          <p className="mt-1 text-2xs text-slate-600" data-testid="linked-na">{below ? <>hidden: {naText("not_profiled", ctx).text}</>
            : sd.signals?.S6?.na_reason ? <>S6 is NA for this wallet: {naText(sd.signals.S6.na_reason, ctx).text}</> : ctx.links?.reason || "no link data in this run"}</p>
        ) : (<>
          {Number(sd.signals?.S6?.raw ?? -1) !== L.linked.length && <p data-testid="link-count-mismatch" className="mt-1 text-2xs font-semibold text-rose-700">
            mismatch: S6 says {sd.signals?.S6?.raw ?? "—"} linked wallets, the list below has {L.linked.length}</p>}
          <p className="mt-1 text-2xs text-slate-600">{L.n_linked} profiled wallets share at least one counterparty with this wallet. Rule: {ctx.links.rule}.
            A shared counterparty is not proof of anything, and linked wallets are not claimed to be one entity or person. Wallets are listed one by one; no groups are formed.</p>
          <div className="mt-2"><Pager id="links-top" page={kpage} pages={kpages} total={L.linked.length} size={ksize} noun="linked wallets" sizeParam="ksize"
            hrefFor={(p, s) => q({ kpage: p, ksize: s })} hidden={hiddenFor("ksize")} /></div>
          <div className="scroll-x mt-2">
            <table className="w-full min-w-[640px] text-2xs" data-testid="link-table">
              <thead className="border-b"><tr><th scope="col" className="th">linked wallet</th><th scope="col" className="th">score</th>
                <th scope="col" className="th text-right">shared counterparties</th><th scope="col" className="th">closest shared counterparties (this wallet · linked wallet: direction, hop)</th></tr></thead>
              <tbody>
                {kshown.map(({ o, n, so, other, shared, oneSided }) => (
                  <tr key={o} className="border-b border-slate-100 align-top" data-testid="link-row" data-linked={o} data-n-shared={n}>
                    <td className="td"><CopyAddress address={o} walletHref={hasWalletPage(run, o) ? href(`/wallet/${o}`, run) : null} />
                      {L.incomplete || other?.incomplete ? <div><S6IncompleteMarker /></div> : null}</td>
                    <td className="td tabular-nums">{so == null ? <NaBadge reason="not_profiled" ctx={ctx} /> : <span className="inline-flex items-center gap-1">{so.toFixed(2)} <TrustIcon ctx={ctx} /></span>}</td>
                    <td className="td text-right tabular-nums">{n}{(shared.length !== n || oneSided) && <span data-testid="link-count-mismatch" className="ml-1 font-semibold text-rose-700">{oneSided ? "(one-sided: not listed by the other wallet)" : `(rebuilt ${shared.length})`}</span>}</td>
                    <td className="td">
                      <ul className="space-y-0.5">{shared.slice(0, 3).map((x) => (
                        <li key={x.c} data-cp={x.c} data-hop={x.hop}><span className="font-mono">{x.c.slice(0, 8)}…{x.c.slice(-4)}</span>{" "}
                          <span className="text-slate-500">{x.a.map(([d, h]) => `${d} hop ${h}`).join(", ")} · {x.b.map(([d, h]) => `${d} hop ${h}`).join(", ")}</span></li>))}</ul>
                      <a className="underline" href={href(`/wallet/${w.wallet}/link/${o}`, run)} data-testid="link-detail">all {n} shared counterparties →</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2"><Pager id="links-bottom" page={kpage} pages={kpages} total={L.linked.length} size={ksize} noun="linked wallets" sizeParam="ksize"
            hrefFor={(p, s) => q({ kpage: p, ksize: s })} hidden={hiddenFor("ksize")} /></div>
        </>)}
      </section>

      <section className="card mt-4" data-testid="scope-table">
        <h2 className="card-title">Anomalous pattern score by scope</h2>
        <div className="mt-2"><Pager id="scopes-top" page={spage} pages={spages} total={scopes.length} size={ssize} noun="scopes" sizeParam="ssize"
          hrefFor={(p, s) => q({ spage: p, ssize: s })} hidden={hiddenFor("ssize")} /></div>
        <div className="scroll-x mt-2">
          <table className="w-full min-w-[640px] text-2xs">
            <thead className="border-b"><tr><th scope="col" className="th">scope</th><th scope="col" className="th">score</th><th scope="col" className="th">signals NA</th><th scope="col" className="th">prefilter</th><th scope="col" className="th">signals</th></tr></thead>
            <tbody>
              {scopes.slice((spage - 1) * ssize, spage * ssize).map((sc) => {
                const d = w.scopes[sc];
                const info = scopeInfo(sc, mk);
                return (
                  <tr key={sc} className={`border-b border-slate-100 ${sc === scope ? "bg-slate-100" : ""}`} data-scope={sc}>
                    <td className="td">{info.market ? <a className="underline-offset-2 hover:underline" href={href(`/market/${info.market}`, run)}>{info.label}</a> : info.label}</td>
                    <td className="td tabular-nums">{d.score == null ? <NaBadge reason="not_profiled" ctx={ctx} /> : <span className="inline-flex flex-wrap items-center gap-1">{d.score.toFixed(2)} <TrustIcon ctx={ctx} />{d.signals?.S6?.incomplete_path && d.signals.S6.raw > 0 ? <S6IncompleteMarker /> : null}</span>}</td>
                    <td className="td">{d.n_signals_na} of 8</td>
                    <td className="td"><PrefilterMarker pass={d.prefilter_pass} /></td>
                    <td className="td">{sc === scope ? <span className="text-slate-500">shown above</span> : <a className="pager-btn" href={`${q({ scope: sc })}#signals`} aria-label={`view signals for ${info.label}`}>view signals</a>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="mt-2"><Pager id="scopes-bottom" page={spage} pages={spages} total={scopes.length} size={ssize} noun="scopes" sizeParam="ssize"
          hrefFor={(p, s) => q({ spage: p, ssize: s })} hidden={hiddenFor("ssize")} /></div>
      </section>

      <section className="card mt-4" data-testid="timeline">
        <h2 className="card-title">Bet timeline by market</h2>
        <p className="text-2xs text-slate-600" data-testid="lanes-shown">{allLanes.length} markets, largest stake first</p>
        <div className="mt-2"><LanePager lpage={lpage} lpages={lpages} total={allLanes.length} lsize={lsize} q={q} /></div>
        {lanes.map((c) => <Lane key={c} cond={c} mk={mk} fills={byCond[c]} pr={prices(run, c)} marketHref={href(`/market/${c}`, run)} hideLabel={!!(ctx.fixture && ctx.plants?.price_label_removed)} />)}
      </section>

      <section className="card mt-4">
        <h2 className="card-title">Bets</h2>
        <p className="text-2xs text-slate-600" data-testid="bet-count">{fills.length} fill rows ({w.fills_note}); {nPost} after chain resolution, shown greyed and excluded from signals.</p>
        <div className="mt-2"><Pager id="bets-top" page={bpage} pages={bpages} total={fills.length} size={bsize} noun="fills" sizeParam="bsize"
          hrefFor={(p, s) => q({ bpage: p, bsize: s })} hidden={hiddenFor("bsize")} /></div>
        <div className="scroll-x mt-2">
          <table className="w-full min-w-[720px] text-2xs" data-testid="bet-table">
            <thead className="border-b"><tr><th scope="col" className="th">time (UTC)</th><th scope="col" className="th">market</th><th scope="col" className="th">outcome</th><th scope="col" className="th">side</th>
              <th scope="col" className="th text-right">size (shares)</th><th scope="col" className="th text-right">price (USDC)</th><th scope="col" className="th text-right">stake (USDC-eq.)</th><th scope="col" className="th">status</th></tr></thead>
            <tbody>
              {fills.slice((bpage - 1) * bsize, bpage * bsize).map((f, i) => (
                <tr key={i} className={`border-b border-slate-100 ${f[6] ? "text-slate-400" : ""}`} data-post-resolution={f[6] ? "1" : "0"}>
                  <td className="td whitespace-nowrap">{fmtT(f[0])}</td>
                  <td className="td max-w-xs truncate" title={mk[f[1]]?.question}>{mk[f[1]]?.question ?? f[1]}</td>
                  <td className="td">{mk[f[1]]?.outcomes?.[String(f[2])] ?? f[2]}</td>
                  <td className="td">{f[3] ? "BUY" : "SELL"}</td>
                  <td className="td text-right tabular-nums">{num(f[4], 2)}</td>
                  <td className="td text-right tabular-nums">{num(f[5], 4)}</td>
                  <td className="td text-right tabular-nums">{num(f[4] * f[5], 2)}</td>
                  <td className="td">{f[6] ? "after resolution (excluded)" : "counted"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2"><Pager id="bets-bottom" page={bpage} pages={bpages} total={fills.length} size={bsize} noun="fills" sizeParam="bsize"
          hrefFor={(p, s) => q({ bpage: p, bsize: s })} hidden={hiddenFor("bsize")} /></div>
      </section>

      <SourceFooter ctx={ctx} extra={<span><span className="text-slate-400">view:</span> wallet file listed in the run&apos;s data manifest</span>} />
    </div>
  );
}
