// Score colour scale (blue 0 -> red 1). NA / not scored has NO colour here: callers render it hatched (G6 U4).
export function scoreColour(s) {
  if (s == null) return null;
  const t = Math.max(0, Math.min(1, s));
  const r = Math.round(59 + t * (220 - 59)), g = Math.round(130 - t * 90), b = Math.round(246 - t * 190);
  return `rgb(${r},${g},${b})`;
}
