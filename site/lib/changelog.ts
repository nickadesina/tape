// Definition changes, newest first. Every change to a threshold in
// config/tape.yaml or a leg in config/universe.yaml gets a line here, so a
// series that moves for a definitional reason says so.

export const CHANGELOG: { date: string; change: string }[] = [
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
