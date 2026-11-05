// Definition changes, newest first. Every change to a threshold in
// config/tape.yaml or a leg in config/universe.yaml gets a line here, so a
// series that moves for a definitional reason says so.

export const CHANGELOG: { date: string; change: string }[] = [
  {
    date: "2026-11-05",
    change:
      "Stack Engine. New: the stack model (a sourced, bottom-up model of the AI supply chain, with every revision " +
      "logged), the daily gaps between what the model says is possible and what the market prices, scenarios, the " +
      "forecast ledger, and research pages. The stress test becomes a monthly exhibit, the fragility of the " +
      "market's view.",
  },
  {
    date: "2026-11-05",
    change:
      "AI Leverage Map. New markets: xyz:KIOXIA and xyz:WDC join memory names; xyz:TSM, xyz:AVGO and xyz:MRVL join " +
      "silicon; new legs Optics (xyz:LITE, xyz:AAOI) and SpaceX / xAI (xyz:SPCX); xyz:MINIMAX and xyz:ZHIPU join labs. " +
      "Leg and complex series move from this hour for that reason alone. The sweep now stores account margin, and " +
      "order-book depth is collected every minute. New: the weekly stress test, the financing map, and the " +
      "one-asset regime, graph-against-market and deal-study series.",
  },
  {
    date: "2026-10-31",
    change:
      "Series start. Legs: neoclouds (xyz:CRWV, xyz:NBIS, xyz:IREN, io:NBIS), memory price (xyz:DRAM, io:DRAM), " +
      "memory names (xyz:MU, xyz:SKHX, xyz:SKHY, xyz:SMSN, xyz:SNDK, xyz:CXMT, io:SNDK), silicon (xyz:NVDA, xyz:AMD, " +
      "xyz:INTC, io:NVDA), compute ETFs (xyz:LYTE, xyz:NCLD), labs (xyz:ANT, io:OAI, io:ANTH). GPU-hour has no live " +
      "market. Starting thresholds: whales top 50, mid floor $25k, fresh under 30 days, bots over 200 fills a day " +
      "with a median hold under 10 minutes, posting coverage 98%.",
  },
];
