// Signal metadata for the UI: short names, one-line plain explanations (D6), units, and why a signal can be NA
// for every wallet in a run (D2 "pending" chip). Labels follow G6 U1: "anomalous pattern score", never a claim about a person.
export const SIGS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"];

export const SIGNAL_NAMES = {
  S1: "fresh wallet", S2: "concentration", S3: "low-odds stake", S4: "pre-event timing",
  S5: "improbable wins", S6: "linked wallets", S7: "coordinated buys", S8: "exit behaviour",
};

export const SIGNAL_EXPLAIN = {
  S1: "How soon after its first activity or funding the wallet placed its first large bet (sooner scores higher).",
  S2: "How much of the wallet's lifetime trading volume went into this set of markets.",
  S3: "How much the wallet staked on low-odds outcomes (price below 0.20) before resolution.",
  S4: "Share of the wallet's stake placed on the winning outcome shortly before the public event.",
  S5: "How unlikely the wallet's record of resolved bets is, given the prices it paid (p-value).",
  S6: "How many other profiled wallets share a funding or destination address with this wallet.",
  S7: "How many other wallets bought the same low-odds outcome within minutes of this wallet.",
  S8: "Whether the wallet redeemed quickly after resolution and then went dormant.",
};

// na_reason -> the input that is missing when a signal is NA for EVERY wallet in the run
export const PENDING_CAUSE = {
  activity_unavailable: "needs wallet profiles",
  stats_unavailable: "needs wallet profiles",
  transfers_unavailable: "needs wallet profiles",
  not_profiled: "needs wallet profiles",
  no_event_ts: "needs public event times",
  event_ts_too_coarse: "needs finer public event times",
};

// UX2 wording (QA-checked against score.py L123-137), shown verbatim
export const NA_EXPLAINER =
  "NA = no data for this signal. It adds nothing to the score and is not a zero measurement. " +
  "Signals with no data for ANY wallet in this run get weight 0; the remaining weights are shared equally.";
