// V4 Cluster view (G6 §1 V4). Clusters are S6 connected components ("linked wallets"); they need wallet profiles.
// QA ruling: until a run with profile inputs is served, the page states that plainly (option i).
import { context, href, resolveRun } from "@/lib/data";
import { RunBar, SourceFooter } from "../components/ui";

export const dynamic = "force-dynamic";

export default async function Clusters({ searchParams }) {
  const sp = await searchParams;
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  return (
    <div>
      <RunBar run={run} ctx={ctx} active="clusters" requested={sp?.run} />
      <h1 className="text-xl font-semibold">Clusters of linked wallets</h1>
      <div className="card mt-3 text-sm" data-testid="clusters-empty">
        <p>No clusters in this run: {ctx.links?.available ? "S6 linked-wallet relation under review; cluster grouping deferred to PROF-002." : "S6 needs profile inputs."}</p>
        {ctx.links?.available && <p className="mt-2 text-2xs text-slate-600">Each wallet page lists that wallet&apos;s linked wallets and the counterparties they share, one link at a time.</p>}
        <p className="mt-2 text-2xs text-slate-600">A cluster groups linked wallets that share funding or destination addresses. A cluster is not proof of anything, and linked wallets are not claimed to be one entity or person.</p>
        <p className="mt-2 text-2xs"><a className="underline" href={href("/", run)}>← leaderboard</a></p>
      </div>
      <SourceFooter ctx={ctx} />
    </div>
  );
}
