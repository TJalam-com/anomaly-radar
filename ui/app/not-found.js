// HTTP 404 page (friendly, normal chrome: banner, nav, run selector — QA A7): reason from the middleware tag.
import { headers } from "next/headers";
import { context, defaultRun } from "@/lib/data";
import { RunBar } from "./components/ui";

export default async function NotFound() {
  const tag = (await headers()).get("x-ar-seg") || "";
  const [kind, state] = tag.split(":");
  const run = defaultRun();
  const ctx = context(run);
  const msg = kind === "wallet"
    ? (state === "invalid" ? "Not a valid wallet address (expected 0x followed by 40 hex characters)." : "Wallet not in this view: only ranked wallets have a page in this build.")
    : kind === "market"
      ? (state === "invalid" ? "Not a valid market id (expected 0x followed by 64 hex characters)." : "Market not in this view.")
      : "Page not found.";
  return (
    <div>
      <RunBar run={run} ctx={ctx} />
      <div data-testid="not-found" data-reason={tag || "route"} className="card max-w-xl">
        <h1 className="text-lg font-semibold">Not found</h1>
        <p className="mt-2 text-sm">{msg}</p>
        <form method="get" action="/wallet" className="mt-3 flex items-center gap-2" role="search">
          <label htmlFor="nf-address" className="sr-only">wallet address</label>
          <input id="nf-address" name="address" placeholder="0x… wallet address" className="w-72 max-w-full rounded border border-slate-300 px-2 py-1 font-mono text-2xs" />
          <button type="submit" className="rounded bg-slate-800 px-2 py-1 text-2xs text-white">find</button>
        </form>
        <p className="mt-3 text-sm"><a href="/" className="underline">← back to leaderboard</a> · <a href="/markets" className="underline">markets</a></p>
      </div>
    </div>
  );
}
