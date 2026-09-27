// "How to use" guide (QA-approved text scratch/qa/anomaly_radar_guide.html, adapted to the app; wording kept). Run-aware: counts,
// signal states and the banner are read from the selected run, and every internal link carries ?run. U1/U13: behaviour, not people;
// U16: no G2E free text. The example row is illustrative only (placeholder address, made-up values).
import { context, href, leaderboard, markets, resolveRun, runs } from "@/lib/data";
import { naText } from "@/lib/na";
import { PENDING_CAUSE, SIGNAL_NAMES, SIGS } from "@/lib/signals";
import { RunBar, SourceFooter, TrustIcon } from "../components/ui";

export const dynamic = "force-dynamic";

const GUIDE_TEXT = {   // signal descriptions: the guide's wording
  S1: "The wallet was funded shortly before its first big bet.",
  S2: "Most of its lifetime activity sits in these few markets.",
  S3: "Large bets on outcomes the market priced below 20%.",
  S4: "Bets placed shortly before the real-world event happened.",
  S5: "Won far more long-shot bets than chance would allow. Shown as “k of n bets won” and a p-value.",
  S6: "Shares funding sources or destinations with other ranked wallets. Currently under review; see the wallet page section.",
  S7: "Bought the same low-odds outcome within minutes of other wallets.",
  S8: "How and when it cashed out winnings.",
};

function Section({ id, title, children }) {
  return (
    <section id={id} className="mt-6 scroll-mt-4">
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="mt-2 space-y-2 text-sm text-slate-800">{children}</div>
    </section>
  );
}

function Card({ title, children }) {
  return <div className="card p-3 text-sm"><b>{title}</b><div className="mt-1 text-slate-700">{children}</div></div>;
}

function Num({ n }) {
  return <span className="mr-1 inline-flex h-4 w-4 items-center justify-center rounded-full bg-slate-800 text-[10px] font-semibold text-white" aria-hidden="true">{n}</span>;
}

export default async function Guide({ searchParams }) {
  const sp = await searchParams;
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const lb = leaderboard(run, "all");
  const nMarkets = Object.keys(markets(run)).length;
  const state = Object.fromEntries(SIGS.map((k) => {
    const withData = lb.rows.some((r) => !r.signals[k].na_reason);
    if (withData) return [k, { data: true }];
    const counts = {};
    for (const r of lb.rows) counts[r.signals[k].na_reason] = (counts[r.signals[k].na_reason] || 0) + 1;
    const reason = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
    return [k, { data: false, reason }];
  }));
  const nData = SIGS.filter((k) => state[k].data).length;
  const pending = SIGS.filter((k) => !state[k].data);
  const all = runs();
  const L = (p) => href(p, run);
  const toc = [["pages", "What each page is for"], ["row", "Reading a wallet row"], ["signals", "The 8 signals"], ["na", "What NA means"],
    ["banners", "Banners and trust labels"], ["wallet", "Wallet and link pages"], ["market", "Market page and bubble map"],
    ["tasks", "Common tasks"], ["not", "What it does not say"]];

  return (
    <div className="max-w-4xl" data-testid="guide">
      <RunBar run={run} ctx={ctx} active="guide" requested={sp?.run} />
      <h1 className="text-xl font-semibold">How to read and use Anomaly Radar</h1>
      <p className="mt-1 text-sm text-slate-700">Anomaly Radar ranks Polymarket wallets by how unusual their betting pattern looks on the US–Iran strike markets.
        This guide explains every part of the screen and how to move around it.</p>
      <nav aria-label="guide sections" className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-2xs">
        {toc.map(([id, t]) => <a key={id} href={`${L("/guide")}#${id}`} className="underline">{t}</a>)}
      </nav>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="guide-facts">
        <Card title={`${lb.n_ranked.toLocaleString("en-US")} wallets ranked`}>of {lb.n_universe.toLocaleString("en-US")} that traded the markets</Card>
        <Card title={`${nMarkets} markets covered`}>US or Israel strikes on Iran and related</Card>
        <Card title={`${nData} of 8 signals have data`}>in this run{pending.length ? `; ${pending.join(", ")} ${pending.length > 1 ? "are" : "is"} pending` : ""}</Card>
        <Card title="Unvalidated">every score, until the one-time held-out test is run</Card>
      </div>

      <Section id="pages" title="What each page is for">
        <p>The top bar has six entries. Every page also carries a run selector, the interim-data banner and a source footer.</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <Card title={<a className="underline" href={L("/")}>Leaderboard</a>}>The ranked list of wallets. Start here. Filter, sort, page through, and open any wallet.</Card>
          <Card title={<a className="underline" href={L("/markets")}>Markets</a>}>All {nMarkets} markets with their outcome and resolution time. Open one to see who held it and the bubble map.</Card>
          <Card title={<a className="underline" href={L("/clusters")}>Clusters</a>}>Deliberately empty for now. Grouping wallets into clusters waits for the new profile set, because the current link data would join most wallets into one misleading group.</Card>
          <Card title={<a className="underline" href={L("/wallet")}>Wallet lookup</a>}>Paste any address to jump to its page. The search box in the top bar does the same.</Card>
          <Card title={<a className="underline" href={L("/about")}>What is this score?</a>}>The in-app explainer: the 8 signals in plain words, how they are weighted in this run, and what NA means.</Card>
          <Card title="How to use">This guide.</Card>
        </div>
      </Section>

      <Section id="row" title="Reading a wallet row">
        <p>Each row of the leaderboard is one wallet. This is an illustrative row, not a real wallet; the numbered parts are explained underneath.</p>
        <div className="card scroll-x p-0" data-testid="guide-example-row">
          <div className="border-b bg-amber-50 px-3 py-1 text-2xs font-semibold text-amber-900">Example row · illustrative values · not a real address</div>
          <table className="w-full min-w-[640px] text-2xs">
            <thead className="border-b bg-slate-50"><tr>
              <th className="th"><Num n={1} />rank</th><th className="th"><Num n={2} />wallet</th><th className="th"><Num n={3} />anomalous pattern score</th>
              <th className="th"><Num n={4} />driven by</th><th className="th"><Num n={5} />signals</th><th className="th text-right"><Num n={6} />low-odds stake</th>
              <th className="th text-right">markets hit</th></tr></thead>
            <tbody><tr>
              <td className="td">12</td>
              <td className="td"><span className="font-mono">0x7a3f…c91e</span> <span className="text-slate-400">(illustrative)</span><div>prefilter: pass</div></td>
              <td className="td"><b>0.71</b> <TrustIcon ctx={ctx} /><div className="text-slate-500">2 of 8 signals NA · ▸ how it adds up</div></td>
              <td className="td">improbable wins · low-odds stake</td>
              <td className="td">S3 0.88 · S5 0.97 · S6 1.00 ◐</td>
              <td className="td text-right">57,011</td><td className="td text-right">14</td>
            </tr></tbody>
          </table>
        </div>
        <ol className="list-none space-y-1 text-sm">
          <li><Num n={1} />Rank among the {lb.n_ranked.toLocaleString("en-US")} ranked wallets. Ties are ordered by address; scores show 2 decimals, so equal-looking scores can still differ slightly.</li>
          <li><Num n={2} />Wallet. The icons copy the full address, open it on Polygonscan, and open its wallet page. “prefilter: pass” means it staked at least 1,000 USDC on low-odds outcomes, the entry rule for being scored.</li>
          <li><Num n={3} />Score from 0 to 1. Higher means the pattern is more unusual. “k of 8 signals NA” tells you how many signals had no data. “How it adds up” opens the exact sum: weight × value for each signal.</li>
          <li><Num n={4} />Driven by names the two signals contributing most. Ties show as “tied (4): … +2”; hover for the rest.</li>
          <li><Num n={5} />Signal values, each from 0 to 1. Only signals that have data in this run are shown; the rest sit in the “signals pending” chip above the table. ◐ marks a link found on an incomplete transfer history.</li>
          <li><Num n={6} />Context figures: money staked on outcomes priced below 0.20 before the market resolved, and how many markets the wallet traded.</li>
        </ol>
      </Section>

      <Section id="signals" title="The 8 signals">
        <p>Each signal looks at one kind of behaviour and gives a value from 0 (nothing unusual) to 1 (very unusual). The score is a weighted sum of them.
          In the current interim run each signal with data carries the same weight; the final weights are chosen later by a pre-registered procedure.</p>
        <div className="grid gap-2 sm:grid-cols-2" data-testid="guide-signals">
          {SIGS.map((k) => (
            <div key={k} className="card p-3 text-sm" data-guide-signal={k} data-has-data={state[k].data ? "1" : "0"}>
              <b>{k} {SIGNAL_NAMES[k]}</b>
              <div className="mt-1 text-slate-700">{GUIDE_TEXT[k]}</div>
              <div className={`mt-1 text-2xs ${state[k].data ? "text-emerald-800" : "text-amber-800"}`}>
                {state[k].data ? "Has data in this run" : `Pending in this run: ${PENDING_CAUSE[state[k].reason] || naText(state[k].reason, ctx).text}`}</div>
            </div>
          ))}
        </div>
      </Section>

      <Section id="na" title="What NA means">
        <p><b>NA means no data for that signal.</b> It adds nothing to the score and is not a zero measurement. A wallet with NA on S6 is not “unlinked”; we simply could not measure it.</p>
        <p>Every NA badge has a plain-language reason. Hover it, or tab to it with the keyboard, to read it. Common reasons:</p>
        <ul className="list-disc space-y-1 pl-6">
          <li><b>not profiled (below prefilter)</b>: the wallet did not stake enough on low-odds outcomes to be fully examined.</li>
          <li><b>no public event time</b>: the market’s real-world event time is not recorded yet, so S4 cannot be computed.</li>
          <li><b>transfer history incomplete</b>: some funding lookups did not finish, so the true value is unknown.</li>
          <li><b>too few resolved bets</b>: S5 needs enough settled bets to judge whether wins are improbable.</li>
        </ul>
        <p>If a signal has no data for any wallet in a run, it is shown once in the “signals pending” chip and gets weight 0; the remaining weights are shared equally.</p>
      </Section>

      <Section id="banners" title="Banners and trust labels">
        <p data-testid="guide-banner"><b>{ctx.banner}</b>. This yellow banner is on every page. {/PROF-001/.test(ctx.profile_label || "") ?
          "It means the numbers come from an early data set whose wallet profiles were fetched by an older, unrecorded version of the code. Treat them as a working preview." :
          "It states which data this run is built from and that the numbers are pre-validation."}</p>
        <Card title={<span><TrustIcon ctx={ctx} /> unvalidated</span>}>On every score. The detector has not yet passed its validation test against known reported and known normal wallets.</Card>
        <Card title="Run selector">Top right. The runs available here: {all.map((r) => r.label + (r.fixture ? " (fixture: made-up test data used by our testers; ignore it)" : "")).join(" · ")}.
          A run without wallet profiles shows the same trades with fewer signals.</Card>
        <Card title="Source footer">Bottom of every page: which data snapshot, weights, settings and profile set produced what you are seeing, by fingerprint. Two screens with the same footer show the same data.</Card>
      </Section>

      <Section id="wallet" title="Wallet and link pages">
        <p>Open a wallet from the leaderboard (the → icon or “wallet page”), from a market page, or by pasting its address into the search box.</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <Card title="Summary cards">Score, rank, markets traded, low-odds stake and trust state at a glance, with the full address.</Card>
          <Card title="Score by scope">The wallet’s score across all markets, in each single market and in each event. Click a market name to open that market.</Card>
          <Card title="Signal cards">Each signal with its value, the evidence behind it, and a one-line explanation. NA cards show their reason.</Card>
          <Card title="Linked wallets (S6)">Other ranked wallets that share at least one funding counterparty with this one, and how many they share. Click a pair to see every shared address, which direction the money moved, and how many steps away it sits.</Card>
          <Card title="Bet timeline">One lane per market: the price over time, this wallet’s buys (green) and sells (red), and a dashed line at resolution. Bets after resolution are greyed out and not counted.</Card>
          <Card title="Bet table">Every fill, 50 per page by default (25, 50 or 100), with side, size, price and stake.</Card>
        </div>
        <p className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-2xs text-amber-900">Links are weak evidence right now. Many links in this data pass through widely shared addresses such as exchanges, relays or deposit services.
          Each link is marked “link found; transfer history incomplete” and the section carries an “S6 under review” note. A redesigned linked-wallets signal is locked in and arrives with the new profile set.</p>
      </Section>

      <Section id="market" title="Market page and bubble map">
        <p>Open <a className="underline" href={L("/markets")}>Markets</a>, then any market. Markets with similar titles are told apart by their event and resolution date columns.</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <Card title="Header">The outcome (or “void”), the on-chain resolution time and transaction, and trading volume in shares and USDC.</Card>
          <Card title="Bubble map">The top 150 wallets in this market. Bubble size is the shares held just before resolution; colour is the anomaly score; grey hatching means no score. Lines join wallets that bought within minutes of each other.</Card>
          <Card title="Side list">Click a wallet to highlight it in the map; use “open wallet page” to go deeper.</Card>
          <Card title="Holder table">Every wallet that held the market, paged, with a “download all (CSV)” link. The file carries its own source details and the unvalidated note.</Card>
        </div>
      </Section>

      <Section id="tasks" title="Common tasks">
        <div className="grid gap-2 sm:grid-cols-2" data-testid="guide-tasks">
          <Card title="See the most unusual wallets"><ol className="list-decimal pl-5"><li>Open <a className="underline" href={L("/")}>Leaderboard</a>.</li><li>Read the top rows; check “driven by” to see why each ranks high.</li><li>Open “how it adds up” to see the exact sum.</li></ol></Card>
          <Card title="Narrow the list"><ol className="list-decimal pl-5"><li>Set “min score”, for example 0.5.</li><li>Pick a signal under “signal has data” to keep only wallets measured on it.</li><li>Press apply. The count line shows “x of {lb.n_ranked} match”.</li></ol></Card>
          <Card title="Look up a specific address"><ol className="list-decimal pl-5"><li>Paste it into the search box in the top bar and press find.</li><li>An unknown or mistyped address shows a clear message instead of an empty page.</li></ol></Card>
          <Card title="Follow a wallet through a market"><ol className="list-decimal pl-5"><li>On the wallet page, click a market name in “score by scope”.</li><li>On the market page, find the wallet in the side list or table.</li><li>Use the browser Back button, or the back link, to return with your filters kept.</li></ol></Card>
          <Card title="Compare with and without profiles"><ol className="list-decimal pl-5"><li>Switch the run selector to the run without wallet profiles.</li><li>The same wallets appear with fewer signals; compare their ranks.</li></ol></Card>
          <Card title="Get the data out"><ol className="list-decimal pl-5"><li>Open a market and use “download all (CSV)”.</li><li>Keep the header lines: they record which run and data the file came from.</li></ol></Card>
        </div>
      </Section>

      <Section id="not" title="What it does not say">
        <ul className="list-disc space-y-1 pl-6">
          <li>A high score is a statistical flag for review, not proof of anything, and it says nothing about who owns a wallet.</li>
          <li>No person is named anywhere in the app, and it never claims that linked wallets belong to one owner.</li>
          <li>Scores are unvalidated. Validation runs once, on a held-out set of known wallets, after the new profile set is fetched and the weights are fixed.</li>
          <li>The app is read-only. It never trades or contacts anyone.</li>
        </ul>
        <h3 className="mt-3 text-sm font-semibold">What changes next</h3>
        <ul className="list-disc space-y-1 pl-6">
          <li>The new profile set is fetched with recorded code versions and completeness checks.</li>
          <li>The redesigned linked-wallets signal and pre-event timing (S4) are scored for the first time.</li>
          <li>The weights are chosen by a fixed, pre-registered procedure.</li>
          <li>The one-time validation test runs; only then can a score show as validated.</li>
        </ul>
      </Section>
      <SourceFooter ctx={ctx} />
    </div>
  );
}
