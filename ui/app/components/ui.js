// Shared server components (D5 design language): run bar (slim banner, nav, address search, run selector),
// summary bar (D4), trust icon (U5, accessible name), NA badge (U4, neutral + focusable), pending chip (D2),
// S6 incomplete marker (U14), prefilter marker (U12), source footer (U3/U14), score bar.
import { href, runs } from "@/lib/data";
import { fmtT } from "@/lib/format";
import { naText } from "@/lib/na";
import { NA_EXPLAINER, PENDING_CAUSE, SIGNAL_NAMES } from "@/lib/signals";
import { scoreColour } from "@/lib/colour";

export { SIGNAL_NAMES };

export function RunBar({ run, ctx, active, requested }) {
  const all = runs();
  const unknownRun = typeof requested === "string" && requested !== "" && requested !== run;
  const nav = [["leaderboard", "/", "Leaderboard"], ["markets", "/markets", "Markets"], ["clusters", "/clusters", "Clusters"],
    ["wallet", "/wallet", "Wallet lookup"], ["about", "/about", "What is this score?"], ["guide", "/guide", "How to use"]];
  return (
    <div className="-mx-4 -mt-4 mb-3 border-b border-slate-200 bg-white sm:mb-4">
      <div data-testid="interim-banner" className={`px-4 py-1.5 text-2xs ${ctx.fixture ? "bg-fuchsia-50 text-fuchsia-900" : "bg-amber-50 text-amber-900"}`}>
        {ctx.banner}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 text-sm">
        <nav className="flex flex-wrap gap-3" data-testid="nav" aria-label="main">
          {nav.map(([k, p, label]) => (
            <a key={k} href={href(p, run)} aria-current={active === k ? "page" : undefined}
              className={`rounded px-1 py-0.5 ${active === k ? "font-semibold text-slate-900 underline decoration-2 underline-offset-4" : "text-slate-600 hover:text-slate-900"}`}>{label}</a>
          ))}
        </nav>
        <form method="get" action="/wallet" className="flex items-center gap-1" data-testid="address-search" role="search">
          {run && <input type="hidden" name="run" value={run} />}
          <label htmlFor="hdr-address" className="sr-only">wallet address</label>
          <input id="hdr-address" name="address" placeholder="0x… wallet address" className="w-56 max-w-[60vw] rounded border border-slate-300 px-2 py-1 font-mono text-2xs"
            pattern="0x[0-9a-fA-F]{40}" title="0x followed by 40 hex characters" />
          <button type="submit" className="rounded bg-slate-800 px-2 py-1 text-2xs text-white">find</button>
        </form>
        {unknownRun && <p data-testid="param-notice" className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-2xs text-slate-700">
          unknown run id “{String(requested).slice(0, 60)}”, showing {ctx.run_label || run}</p>}
        <form method="get" className="ml-auto flex items-center gap-1 text-2xs" data-testid="run-selector">
          <label htmlFor="run" className="text-slate-500">run</label>
          <select id="run" name="run" defaultValue={run} className="max-w-[60vw] rounded border border-slate-300 px-1 py-1">
            {all.map((r) => <option key={r.id} value={r.id}>{r.label}{r.fixture ? " (fixture)" : ""}</option>)}
          </select>
          <button type="submit" className="pager-btn">switch</button>
        </form>
      </div>
    </div>
  );
}

export function SummaryBar({ ctx, items }) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-2xs text-slate-700 sm:mb-3 sm:px-4 sm:py-2" data-testid="summary-bar">
      {items.map(([k, v], i) => <span key={i}><span className="text-slate-500">{k}</span> <b className="font-semibold">{v}</b></span>)}
      <span><span className="text-slate-500">trust</span> <TrustIcon ctx={ctx} /></span>
      <span><span className="text-slate-500">run</span> {fmtT(Date.parse(ctx.finished_at) / 1000 || null)}</span>
    </div>
  );
}

export function TrustIcon({ ctx }) {
  // G6 U5 / §2b / C0-5: "validated" only when G5 passed AND this run's weights are the G5-passed weights.
  const validated = ctx?.g5_passed_weights_sha256 && ctx.g5_passed_weights_sha256 === ctx.weights_sha256;
  const text = validated ? `validated at G5 ${ctx.g5_report_sha256?.slice(0, 8)}…` : "unvalidated — detector not yet validated";
  return (
    <span data-trust={validated ? "validated" : "unvalidated"} className="trust-icon" title={text} aria-label={text} role="img">
      <span aria-hidden="true">{validated ? "✓" : "◌"}</span><span aria-hidden="true">{validated ? "validated" : "unvalidated"}</span>
    </span>
  );
}

export function PrefilterMarker({ pass }) {
  const v = pass ? "pass" : "fail";
  return <span data-prefilter={v} className="text-2xs text-slate-600">prefilter: <b className={pass ? "text-slate-800" : "text-slate-500"}>{v}</b></span>;
}

export function NaBadge({ reason, ctx, full = false }) {
  const { text, known } = naText(reason, ctx);
  // neutral grey (F4); focusable so the reason is reachable by keyboard/touch (UX2); full text shown where space allows
  return (
    <span tabIndex={0} role="note" aria-label={text} title={text} data-na-reason={reason} data-na-known={known ? "1" : "0"} className="na-badge">
      {full ? text : "NA"}
    </span>
  );
}

export function PendingChip({ pending, ctx }) {
  // D2/UX2: signals NA for EVERY wallet in this run, shown once, with the byte-exact §2a text per signal
  if (!pending.length) return null;
  const groups = {};
  for (const p of pending) (groups[PENDING_CAUSE[p.reason] || "missing inputs"] ||= []).push(p);
  const summary = Object.entries(groups).map(([cause, ps]) => `${ps.map((p) => p.sig).join(" ")} ${cause}`).join(" · ");
  return (
    <details className="card mb-3 p-3" data-testid="pending-chip">
      <summary className="cursor-pointer text-sm">
        <span className="chip mr-2">{pending.length} signals pending in this run</span>{summary}
      </summary>
      <p className="mt-2 text-2xs text-slate-700" data-testid="na-explainer">{NA_EXPLAINER}</p>
      <ul className="mt-2 grid gap-1 text-2xs text-slate-700 md:grid-cols-2">
        {pending.map((p) => (
          <li key={p.sig} data-pending-signal={p.sig} data-na-reason={p.reason}>
            <b>{p.sig} {SIGNAL_NAMES[p.sig]}</b>: {naText(p.reason, ctx).text}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** U14 / C0-12: a positive S6 that rests on an incomplete expansion path carries a visible marker. */
const S6_INCOMPLETE = "link found; transfer history incomplete";
// QA S6-V1 (2026-09-27): shown wherever S6 links are listed while the S6 definition is under review
export function S6UnderReview({ aboutHref }) {
  return (
    <p data-testid="s6-under-review" className="mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-1.5 text-2xs text-amber-900">
      S6 definition under review: many links in this run pass through widely shared addresses (likely exchanges, relays or deposit services),
      so a link here is weak evidence. See <a className="underline" href={aboutHref}>&lsquo;What is this score?&rsquo;</a>
    </p>
  );
}
export function S6IncompleteMarker({ compact = false }) {
  // compact (leaderboard cells, A10): same marker attribute, full text as tooltip + screen-reader text
  if (compact) return <span data-s6-incomplete="1" title={S6_INCOMPLETE} className="ml-0.5 cursor-help text-slate-600">◐<span className="sr-only"> {S6_INCOMPLETE}</span></span>;
  return <span data-s6-incomplete="1" className="ml-1 rounded border border-slate-300 bg-slate-50 px-1 text-2xs text-slate-700">{S6_INCOMPLETE}</span>;
}

export function ScoreBar({ score }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return (
    <span className="inline-block h-1.5 w-16 rounded bg-slate-200 xl:w-24" aria-hidden="true">
      <span className="block h-1.5 rounded" style={{ width: `${pct}%`, background: scoreColour(score) }} />
    </span>
  );
}

export function profileText(label) {
  if (!label) return "none";
  return /^PROF-001/.test(label) ? `${label} — plumbing only, not final` : label;
}

const NOT_IN_RUN = "not in this run";
const short8 = (h) => (h ? `${h.slice(0, 8)}…` : NOT_IN_RUN);

export function SourceFooter({ ctx, extra }) {
  const snap = ctx.snapshots.find((s) => s.id === "SNAP-003") || ctx.snapshots[0];
  const step4 = ctx.snapshots.find((s) => s.id.endsWith("step4"));
  const plumbing = /^PROF-001/.test(ctx.profile_label || "");
  const items = [
    ["run", ctx.run_id, ctx.run_id],
    ["run time (UTC)", ctx.finished_at, ctx.finished_at],
    [snap?.id || "snapshot", short8(snap?.manifest_sha256), snap?.manifest_sha256],
    ["step-4", short8(step4?.manifest_sha256), step4?.manifest_sha256],
    ["weights", ctx.weights_version ? `${ctx.weights_version} ${short8(ctx.weights_file_sha256)}` : NOT_IN_RUN,
      ctx.weights_version ? `${ctx.weights_version} file ${ctx.weights_file_sha256 || NOT_IN_RUN}; derived ${ctx.weights_sha256 || NOT_IN_RUN}` : NOT_IN_RUN],
    ["params", short8(ctx.params_file_sha256), ctx.params_file_sha256],
    ["event times", ctx.event_times_sha256 ? short8(ctx.event_times_sha256) : "none (S4 NA)", ctx.event_times_sha256 || "none"],
    ["profile", profileText(ctx.profile_label), ctx.profile_label || "none"],
    ["determinism gate", ctx.determinism_gate_pass ? "pass" : "not run / fail", String(ctx.determinism_gate_pass)],
  ];
  return (
    <div data-testid="source-footer" data-profile-final={plumbing ? "0" : "n/a"}
      className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-200 pt-2 text-2xs text-slate-500">
      {items.map(([k, v, full]) => <span key={k} title={full ?? NOT_IN_RUN}><span className="text-slate-400">{k}:</span> {v ?? NOT_IN_RUN}</span>)}
      {extra}
    </div>
  );
}
