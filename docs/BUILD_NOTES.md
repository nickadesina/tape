# Build notes

What the build found on chain on 2026-10-31, and where the code departs from the design doc (Oct 30, 2026) or
settles something the doc left open.

## Day 1 check: are counterparties on HIP-3 fills?

Yes. The WebSocket `trades` channel for `xyz:` and `io:` coins carries `users: [buyer, seller]` on every fill. The
design stands as written.

## Universe, as resolved against live `meta`

| Leg | Markets | Change from the doc |
|---|---|---|
| Neoclouds | xyz:CRWV, xyz:NBIS, xyz:IREN, io:NBIS | The open question is answered: xyz lists NBIS and IREN. |
| Memory ETF | xyz:DRAM, io:DRAM | xyz:DRAM is the Roundhill Memory ETF (Cboe: DRAM), not a DRAM price index; the leg was first mislabeled "Memory price". |
| Memory names | xyz:MU, xyz:SKHX, xyz:SKHY, xyz:SMSN, xyz:SNDK, xyz:CXMT, io:SNDK | |
| Silicon | xyz:NVDA, xyz:AMD, xyz:INTC, io:NVDA | |
| Compute ETFs | xyz:LYTE, xyz:NCLD | |
| Labs | xyz:ANT, io:OAI, io:ANTH | xyz has no `OPENAI` or `ANTHROPIC`. `xyz:ANT` is the Anthropic pre-IPO perp. |
| GPU-hour | xyz:H100, io:H100 | Both are delisted. They join automatically if relisted. |

EntropyIO also lists DRAM, SNDK, NVDA and NBIS. These are the same bets on a second venue, so they sit in the same
legs. Their OI is small, so they barely move the leg numbers. `tape universe` lists the live candidates that fit the
complex but are not in it: KIOXIA, WDC, MINIMAX, ZHIPU, SMH, SOXL, AVGO, MRVL, ARM, TSM, ASML, LITE, AAOI. Moving one
into a leg in `config/universe.yaml` is the whole change.

The doc's volume figures did not match the day of the build. On 2026-10-31, xyz:DRAM showed about $11M of 24h volume
and $79M of OI; the doc says $118M to $170M a day and about $72M OI. The OI matches; the volume is whatever the day
was.

## Margin is not always isolated

`clearinghouseState` on the xyz dex returns cross-margin positions (`leverage.type = "cross"`) for some markets. So
"HIP-3 margin is isolated per market" is not true everywhere. It changes nothing in the build: positions are still
one-way, so one address on one market has one size and one sign. Liquidation prices for cross positions depend on the
whole account, which the liquidation candidate check already allows for: it only proposes candidates and confirms
them from fill history.

## Reconciling the doc's coverage rules

The doc says three things about coverage under 98%:

- The metrics job refuses to write a daily row.
- A market's line is drawn dashed with its number shown, and the post still goes out.
- There is no daily post when complex-level coverage is under the bar.

The build does all three:

- Hourly `series` rows are always written, and each carries its subject's coverage.
- `daily_series` rows are written only for subjects at or above the bar.
- Lines are dashed wherever coverage is under the bar.
- The daily job sends the gap-day line instead of a chart when the complex is under the bar.

## The daily point is the 15:00 ET snapshot

A sweep starts at the top of the hour and takes minutes. The 16:00 ET render therefore cannot include the 16:00 ET
sweep, so it uses the 15:00 ET snapshot. That is the last full sweep before the post, and the hour the daily rows
come from (`daily.snapshot_hour_et`). If that hour's metrics are missing, the job falls back to the latest hour
before 16:00 ET and says so in the caption.

## Coverage bootstrapping: leaderboard seeding

Addresses are discovered from fills. Someone who opened a large position before the indexer started and has not
traded since is invisible, and coverage stalls well under 98% until they trade. `tape seed-leaderboard` queues every
address on Hyperliquid's public leaderboard above an account-value floor for a single low-priority snapshot per dex.
Those holding a complex position join the sweep set. This was not in the doc. It uses only public data, and it is
what makes the 98% bar reachable in weeks rather than months.

A related fix: the day's whale set is fixed from the 15:00 ET snapshot. During bootstrap that snapshot covers a
fraction of the market, so a set computed under the coverage bar is recomputed every hour until coverage clears it.

## Fresh wallets and the 10,000-fill window

Hyperliquid serves only an address's most recent 10,000 fills. The first-fill estimate fetches the earliest
available page of fills. If that page is short, the whole history was visible and the first complex fill is exact.
If not, an address whose earliest visible complex fill is recent cannot be dated, and it is not counted fresh. The
overlay understates rather than invents.

## Liquidations

The `trades` channel does not mark liquidations. A liquidation is first proposed as a candidate: an address the tape
has snapshotted shrinks its position on a fill within 1% of its last snapshot liquidation price. It is then confirmed
from that address's `userFillsByTime`, whose fills carry a `liquidation` object naming the liquidated user. This
misses liquidations of positions opened since the last snapshot, so liquidation share is a floor. The method page
says so.

## Rate limits in practice

The info budget is 1,020 weight a minute (85% of 1,200). The sweep, market contexts, discovery snapshots and
liquidation confirmations run at high priority. History and leaderboard seeding run at low priority and only while
the bucket is above 30%, so they never delay the sweep.

First live run (2026-10-31): the 19:00 UTC sweep had only the 19 addresses seen trading in the first minute,
and 0.1% coverage. After seeding the leaderboard at $1M+ account value (2,854 addresses, about 440 snapshots a
minute), the 20:00 sweep covered 996 address-dex pairs and 2,232 positions in 107 s with no errors, and complex
coverage reached 28.6%. Every cohort net summed back to the imbalance (complex: whale −6.5%, mid +1.7%, small 0.0%
against an imbalance of −4.8%). Seeding then continued at the $25k floor (18,009 addresses).

First-fill history is the expensive part: each page of 2,000 fills costs 120 weight. It drains over hours in the
background and is ordered by position size, so whales are dated first.

## Bots need a day of fills

Over the first 40 minutes, the bot rule (over 200 fills a day, median hold under 10 minutes) flagged 32 addresses.
Over a window that short, any active trader both trades often and closes quickly. Bot classification now waits until
the tape has watched fills for `bot_min_observed_days` (1). Until then, nobody is a bot.

## Running on Vercel instead of the box

The doc puts the indexer on the Lightsail box. It now runs on Vercel (README, Deploy), which cannot hold a process
open: a function stops after 800 s. What changed, and what it costs in fidelity:

- **The WebSocket is held by overlapping shifts.** A shift starts every 10 minutes and lasts 11.5. If a shift
  fails to start, the next one sees the gap and backfills from `userFillsByTime`, which is slow (20 weight per
  address) and bounded by the shift's length, so a long outage leaves a hole in `fills`. Positions are unaffected;
  they come from the sweep.
- **Running positions restart each shift** from the last sweep plus the fills since it finished. Addresses first
  seen by an earlier shift in the same hour are not "known" again until the next sweep, so a real-time flip or entry
  alert for a brand-new address can wait up to an hour; the hourly snapshot events still catch it.
- **The sweep is resumable.** It is committed in chunks of 200 pairs and continues on the next call, so it is no
  longer bounded by one function. A sweep that spans two calls is a snapshot taken over up to 20 minutes rather than
  a few; one that is never finished is closed when the next hour's starts, at whatever coverage it reached.
- **Two functions share the rate limit.** Each has its own bucket. While a sweep runs, the sweep takes 75% of the
  budget and the shift the rest. Vercel's outbound IPs are shared with other tenants, so occasional 429s are
  expected; the client backs off and retries.

## Not built, on purpose

The doc's out-of-scope list holds: no trade button, no accounts, no leaderboard, no launch. Two things are also left
outside this repo:

- Posting to X is a human act.
- twitter-checker lives in its own repo. It gets the matching chart through `tape match`, which it can call as a
  command or import as `tape.match.match_text`.

## First two weeks (build plan, steps 5 and 6)

- **Watch coverage climb:** `tape status` shows it per market. The site shows the dashed lines.
- **Tune thresholds against real distributions:** edit `config/tape.yaml`, add a line to `site/lib/changelog.ts`,
  and recompute with `tape metrics --hour …` if the history should change.
- **Write a take each day without posting** until the 30-day window is at least half full and the complex clears
  98%.
