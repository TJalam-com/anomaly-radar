// G6 r6 §2a: na_reason -> plain text (U4). NA is never 0. Unknown reasons render raw in brackets + a UI health flag (C0-6).
export const NA_TEXT = {
  not_profiled: "NA — not profiled (below prefilter)",
  no_first_ts: "NA — no activity or funding found",
  funding_window_truncated: "NA — funding history incomplete",
  no_lifetime_volume: "NA — no lifetime volume",
  no_event_ts: "NA — no public event time recorded",
  event_ts_too_coarse: "NA — event time too coarse for the window",
  no_winner: "NA — market resolved 50/50 (no winner)",
  no_buys: "NA — no buys in this market",
  "n_resolved<min": "NA — fewer than {s5_min_bets} resolved bets",
  no_transfers_found: "NA — no collateral transfers found",
  rpc_unavailable: "NA — chain data unavailable this run",
  transfers_unverified: "NA — transfer history could not be verified complete",
  no_winning_bet: "NA — no winning bet",
  dormancy_unobservable: "NA — too soon after redemption to judge",
  auto_redeem_unknown: "NA — redemption type not identified",
  no_resolution_ts: "NA — market not resolved on chain",
  // QA ruling 2026-09-27 (b); Planner adds these to G6 §2a in r7
  activity_unavailable: "NA — wallet activity unavailable this run",
  stats_unavailable: "NA — lifetime stats unavailable this run",
  transfers_unavailable: "NA — transfer history unavailable this run",
};

export function naText(reason, ctx) {
  if (reason in NA_TEXT) {
    return { text: NA_TEXT[reason].replace("{s5_min_bets}", String(ctx?.s5_min_bets ?? "?")), known: true };
  }
  return { text: `NA — [${reason}]`, known: false };
}
