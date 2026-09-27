// /wallet — wallet lookup (principal's finding): address input (0x + 40 hex, lower-cased), link back, and a clear
// "not in this view" state for unknown addresses.
import { context, hasWalletPage, href, isAddress, resolveRun } from "@/lib/data";
import { redirect } from "next/navigation";
import { RunBar, SourceFooter } from "../components/ui";

export const dynamic = "force-dynamic";

export default async function WalletLookup({ searchParams }) {
  const sp = await searchParams;
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const raw = String(sp?.address ?? "").trim();
  const addr = raw.toLowerCase();
  const valid = isAddress(addr);
  if (valid && hasWalletPage(run, addr)) redirect(href(`/wallet/${addr}`, run));
  return (
    <div data-testid="wallet-lookup">
      <RunBar run={run} ctx={ctx} active="wallet" requested={sp?.run} />
      <a href={href("/", run)} className="text-2xs underline">← leaderboard</a>
      <h1 className="mt-1 text-xl font-semibold">Wallet lookup</h1>
      <form method="get" className="card mt-3 flex flex-wrap items-center gap-2" role="search">
        <label htmlFor="lookup-address" className="text-2xs text-slate-600">wallet address</label>
        <input type="hidden" name="run" value={run} />
        <input id="lookup-address" name="address" defaultValue={raw} placeholder="0x… (40 hex characters)" className="w-full max-w-[28rem] rounded border border-slate-300 px-2 py-1 font-mono text-sm"
          pattern="0x[0-9a-fA-F]{40}" title="0x followed by 40 hex characters" required />
        <button type="submit" className="rounded bg-slate-800 px-3 py-1 text-sm text-white">open</button>
      </form>
      {raw && !valid && <p data-testid="lookup-invalid" className="mt-3 text-sm text-rose-700">Not a wallet address: expected 0x followed by 40 hex characters.</p>}
      {valid && <p data-testid="wallet-missing" className="mt-3 text-sm text-slate-700">Wallet not in this view: only ranked wallets (anomalous pattern score not NA in scope all) have a page in this build.</p>}
      <SourceFooter ctx={ctx} />
    </div>
  );
}
