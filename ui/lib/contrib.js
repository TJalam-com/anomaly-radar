// UX3 / L2 (QA A2): per-signal contributions weight × component, printed TRUE (no rescaling to the stored total).
// The displayed total is the stored total; if |Σ(w×c) − total| > 1e-9 the page shows a visible mismatch flag.
import { SIGS } from "./signals";

export function contributions(signals, weights) {
  return SIGS.map((k) => {
    const s = signals[k] || {};
    const w = weights?.[k] ?? 0;
    const na = !!s.na_reason || s.component == null;
    return { sig: k, weight: w, component: na ? null : s.component, na, value: na ? 0 : w * s.component };
  });
}

export function breakdown(signals, weights, total) {
  const parts = contributions(signals, weights);
  const sum = parts.reduce((a, p) => a + p.value, 0);
  return { parts, sum, mismatch: Math.abs(sum - total) > 1e-9 };
}

/** weight as an exact small fraction when it is one (1/3, 1/2, 1/5 …), else 4 dp */
export function fmtWeight(w) {
  for (let d = 1; d <= 12; d++) {
    const n = Math.round(w * d);
    if (n > 0 && Math.abs(w - n / d) < 1e-12) return d === 1 ? String(n) : `${n}/${d}`;
  }
  return w.toFixed(4);
}

/** QA N1: the n largest weight x component products. Ties are judged on the product as PRINTED in the breakdown
 *  (4 dp), so a tie the reader can see is never broken silently: when the n-th place is tied, every signal at that
 *  printed value goes to `tied`, and `top` keeps only those printed strictly above it. A product that prints as 0.0000 is
 *  never named. */
export function drivers(parts, n = 2) {
  const shown = (x) => Number(x.value.toFixed(4));
  const s = [...parts].filter((x) => shown(x) > 0).sort((a, b) => b.value - a.value || a.sig.localeCompare(b.sig));
  if (s.length <= n || shown(s[n]) !== shown(s[n - 1])) return { top: s.slice(0, n), tied: [] };
  const v = shown(s[n - 1]);
  return { top: s.filter((x) => shown(x) > v), tied: s.filter((x) => shown(x) === v).sort((a, b) => a.sig.localeCompare(b.sig)) };
}
