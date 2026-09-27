// Formatting helpers (display only).
export const fmtT = (u, withSec = false) =>
  u == null ? "—" : new Date(u * 1000).toISOString().replace("T", " ").slice(0, withSec ? 19 : 16) + " UTC";
export const fmtD = (u) => (u == null ? "—" : new Date(u * 1000).toISOString().slice(0, 10));
export const num = (x, d = 2) => (x == null ? "—" : Number(x).toLocaleString("en-US", { maximumFractionDigits: d }));
export const short = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "—");

/** Parse an integer query param with clamping; returns { value, invalid } (F7). */
export function intParam(raw, { def, min, max }) {
  if (raw == null || raw === "") return { value: def, invalid: false, clamped: false };
  const n = Number(raw);
  if (!Number.isInteger(n)) return { value: def, invalid: true, clamped: false };
  if (n < min) return { value: min, invalid: false, clamped: true };
  if (n > max) return { value: max, invalid: false, clamped: true };
  return { value: n, invalid: false, clamped: false };
}

export const PAGE_SIZES = [25, 50, 100];
