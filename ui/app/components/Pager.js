// D1/UX1 pager (server component): first / prev / numbered pages with ellipsis / next / last, "rows x–y of N",
// page-size selector; all state in the URL. Rendered above AND below every paged table.
import { PAGE_SIZES } from "@/lib/format";

function pageList(page, pages) {
  const set = new Set([1, pages, page - 2, page - 1, page, page + 1, page + 2].filter((p) => p >= 1 && p <= pages));
  const list = [...set].sort((a, b) => a - b);
  const out = [];
  list.forEach((p, i) => { if (i && p - list[i - 1] > 1) out.push("…"); out.push(p); });
  return out;
}

export default function Pager({ id, page, pages, total, size, hrefFor, hidden = {}, noun = "rows", sizeParam = "size" }) {
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(total, page * size);
  const link = (p, label, aria, disabled) => disabled
    ? <span className="pager-btn opacity-40" aria-disabled="true">{label}</span>
    : <a className="pager-btn" href={hrefFor(p, size)} aria-label={aria}>{label}</a>;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs" data-testid={`pager-${id}`}>
      <nav aria-label={`${noun} pages (${id})`} className="flex flex-wrap items-center gap-1">
        {link(1, "« first", "first page", page <= 1)}
        {link(page - 1, "‹ prev", "previous page", page <= 1)}
        {pageList(page, pages).map((p, i) => p === "…"
          ? <span key={`e${i}`} className="px-1 text-slate-400">…</span>
          : p === page
            ? <span key={p} className="pager-btn pager-current" aria-current="page">{p}</span>
            : <a key={p} className="pager-btn" href={hrefFor(p, size)} aria-label={`page ${p}`}>{p}</a>)}
        {link(page + 1, "next ›", "next page", page >= pages)}
        {link(pages, "last »", "last page", page >= pages)}
      </nav>
      <span className="text-slate-600" data-testid={`pager-${id}-range`}>{total === 0 ? `no ${noun} match` : `${noun} ${from}–${to} of ${total}`}</span>
      <form method="get" className="flex items-center gap-1">
        {Object.entries(hidden).filter(([, v]) => v != null && v !== "").map(([k, v]) => <input key={k} type="hidden" name={k} value={v} />)}
        <label className="text-slate-500" htmlFor={`${id}-size`}>per page</label>
        <select id={`${id}-size`} name={sizeParam} defaultValue={String(size)} className="rounded border px-1 py-1">
          {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="submit" className="pager-btn">set</button>
      </form>
    </div>
  );
}
