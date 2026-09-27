// V4 ego view, one link (QA (b)-lite): the COMPLETE set of counterparties two linked wallets share, with direction and
// hop on each side, paged. No transitive grouping; the S6-incomplete marker on the link when the path is incomplete.
import { notFound } from "next/navigation";
import { context, hasWalletPage, href, isAddress, resolveRun, sharedCounterparties, walletLinks } from "@/lib/data";
import { intParam, PAGE_SIZES } from "@/lib/format";
import CopyAddress from "../../../../components/CopyAddress";
import Pager from "../../../../components/Pager";
import { RunBar, S6IncompleteMarker, S6UnderReview, SourceFooter } from "../../../../components/ui";

export const dynamic = "force-dynamic";

const sides = (L) => L.map(([d, h]) => `${d} hop ${h}`).join(", ");

export default async function LinkPage({ params, searchParams }) {
  const { address, other } = await params;
  const sp = await searchParams;
  const a = String(address).toLowerCase(), b = String(other).toLowerCase();
  if (!isAddress(a) || !isAddress(b)) notFound();
  const run = resolveRun(sp?.run);
  const ctx = context(run);
  const A = walletLinks(run, a), B = walletLinks(run, b);
  const entry = A?.linked.find(([o]) => o === b);
  if (!A || !B || !entry) notFound();
  const shared = sharedCounterparties(A, B, ctx.links?.stop_list || []);
  const size = PAGE_SIZES.includes(Number(sp?.size)) ? Number(sp.size) : 50;
  const pages = Math.max(1, Math.ceil(shared.length / size));
  const page = intParam(sp?.page, { def: 1, min: 1, max: pages }).value;
  const listHref = (p, s) => href(`/wallet/${a}/link/${b}?${new URLSearchParams({ ...(p > 1 ? { page: String(p) } : {}), ...(s !== 50 ? { size: String(s) } : {}) })}`, run);
  const pagerProps = { page, pages, total: shared.length, size, hrefFor: listHref, noun: "shared counterparties", hidden: { run } };
  return (
    <div data-testid="link-page" data-a={a} data-b={b}>
      <RunBar run={run} ctx={ctx} active="wallet" requested={sp?.run} />
      <a href={href(`/wallet/${a}#linked`, run)} className="text-2xs underline">← wallet</a>
      <h1 className="mt-1 text-xl font-semibold">Shared counterparties of two linked wallets</h1>
      <S6UnderReview aboutHref={`${href("/about", run)}#s6-review`} />
      <div className="mt-1 grid gap-1 text-2xs sm:grid-cols-2">
        <div>this wallet: <CopyAddress address={a} walletHref={hasWalletPage(run, a) ? href(`/wallet/${a}`, run) : null} /></div>
        <div>linked wallet: <CopyAddress address={b} walletHref={hasWalletPage(run, b) ? href(`/wallet/${b}`, run) : null} /></div>
      </div>
      <p className="mt-2 text-2xs text-slate-600">{entry[1]} shared counterparties. Rule: {ctx.links?.rule}. A shared counterparty is not proof of anything,
        and the two wallets are not claimed to be one entity or person.{A.incomplete || B.incomplete ? <> <S6IncompleteMarker /></> : null}
        {shared.length !== entry[1] && <b data-testid="link-count-mismatch" className="ml-1 text-rose-700">rebuilt list has {shared.length}, export says {entry[1]}</b>}
        {!B.linked.some(([x]) => x === a) && <b data-testid="link-count-mismatch" className="ml-1 text-rose-700">one-sided: the linked wallet does not list this wallet</b>}</p>
      <div className="mt-2"><Pager id="shared-top" {...pagerProps} /></div>
      <div className="card scroll-x mt-2 p-0">
        <table className="w-full min-w-[560px] text-2xs" data-testid="shared-table">
          <thead className="border-b bg-slate-50"><tr><th scope="col" className="th">counterparty</th><th scope="col" className="th">this wallet (direction, hop)</th>
            <th scope="col" className="th">linked wallet (direction, hop)</th></tr></thead>
          <tbody>
            {shared.slice((page - 1) * size, page * size).map((x) => (
              <tr key={x.c} className="border-b border-slate-100" data-testid="shared-row" data-cp={x.c} data-hop={x.hop}>
                <td className="td"><CopyAddress address={x.c} /></td><td className="td">{sides(x.a)}</td><td className="td">{sides(x.b)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2"><Pager id="shared-bottom" {...pagerProps} /></div>
      <SourceFooter ctx={ctx} />
    </div>
  );
}
