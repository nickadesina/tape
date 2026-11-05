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

## Leverage map (design doc, Nov 5 2026)

What the build of the AI Leverage Map found and decided, in the order of the doc's "Changes to the existing Tape".

### Sweep: account margin

`clearinghouseState` already carries the margin summary; the sweep used to drop it. It now writes an `accounts` row
(account value, cross account value, margin used, cross maintenance margin, withdrawable, and cross/isolated/mixed over
the complex positions) in the same chunk transaction as `snapshots`, and each snapshot row gains its margin type,
margin used and position value. Margin on a HIP-3 dex is per dex, so the row is keyed `(hour, address, dex)`.
Rows are written only for pairs with a position in the complex; their totals include positions outside it, which the
stress test holds at their snapshot price.

### Depth: three views of one book

`l2Book` sends at most 20 levels a side. A live check on 2026-11-05: at full precision xyz:CRWV's book reached 0.15%
from mid, io:ANTH's 0.4%. At 3 significant figures the same books reached about 4% (CRWV) and 10% (ANTH); at 2,
past 40%. So the collector subscribes to each book three times (`depth.sig_figs: [full, 3, 2]`), on three sockets
because an l2Book message does not say which aggregation it belongs to, and stitches them: finest first, then each
coarser view beyond the last one's reach. A coarse level can double-count up to one price bucket where two views meet.
Bands (2%, 5%, 10% each side, in USD) are written every minute with how far the book reached; one full book per
market per hour goes to `books`, which the stress test walks.

The doc's 5 November figure of about $1.1M of bids within 5% on io:ANTH read $2.0M in the stitched book a few hours
later. Depth moves by hour, which is why the stress test runs on a range of books (below).

Size: about 35 markets x 1,440 minutes = 50k depth rows a day plus 840 books. With hourly account rows this outgrows
Neon's free 0.5 GB in days, as the doc says; the paid plan is an open question for the operator.

### Universe

All ten names the doc lists are live. OI on 2026-11-05: xyz:SPCX $162M, xyz:ZHIPU $18M, xyz:LITE $16M, xyz:MRVL $23M,
xyz:AVGO $9.5M, xyz:TSM $7.8M, xyz:KIOXIA $6.3M, xyz:MINIMAX $2.5M, xyz:AAOI $2.5M, xyz:WDC $2.0M.
KIOXIA and WDC joined memory names; TSM, AVGO and MRVL silicon; MINIMAX and ZHIPU labs; LITE and AAOI a new Optics
leg. SPCX got its own leg (SpaceX / xAI): its OI is larger than every lab market's but one, and most of what it
prices is launch and Starlink, so in Labs it would have drowned the leg. Leg and complex series move from this hour
for definitional reasons; the site changelog says so.

### Graph

`config/graph.yaml` gives every universe market a node (the stress test shocks names, not tickers: Anthropic is
xyz:ANT and io:ANTH together) and holds hyperscalers as counterparties without markets. Following the doc, an agent
drafted 62 edges from filings and reporting with a source on each (31 SEC filings, 22 company releases, 9 reports)
and seven counterparty nodes (lenders, SoftBank, Cerebras). Every one ships with `verified_at: null`, so none is
loaded until a person checks it against its source. The agent flagged the ones to check hardest in the notes: the
$100B Nvidia-OpenAI LOI is probably superseded by the $30B February 2026 investment; Oracle-OpenAI $300B rests on
WSJ reporting; the 34.5% SpaceX revenue share is filed but its customer is named as Anthropic only by Bloomberg;
warrant edges run holder to issuer.

A dry run with every draft treated as verified (nothing stored) exercised the engines on real closes as of the
2026-11-04 UTC close: Anthropic's dependents are Microsoft, Google, Amazon, AMD and SpaceX at one hop and CoreWeave,
Nebius and IREN at two; their sell-off betas to Anthropic run 0.56 (SPCX) to 1.81 (IREN), so a graph shock moves the
neoclouds further than the lab; the SpaceX revenue-share edge shows a 14% beta against a 34.5% share, a gap at the
29th percentile of its own short history; 54 deal moves had Hyperliquid prices on the announcement day. Until then the graph shocks, graph-against-market and deal
studies have nothing to read and stay empty; `tape graph --drafts` lists the queue. The loader refuses the whole file
on any malformed edge (unknown node, type or confidence, no link, a revenue share without a share): a wrong edge
drives a wrong claim.

### Stress test

The model is in `tape/stress.py` with its simplifications written at the top: maintenance margin is 1/(2 x max
leverage) without tiers; liquidated positions close in full at market (Hyperliquid liquidates large positions
partially, and its backstop takes what the book cannot), so forced selling is an upper bound and flow past the book's
reach is reported as unabsorbed; the book re-forms around the shocked price and does not refill between rounds.

Same-hour depth: each scenario runs on the stored book scaled to the 90th, 50th and 10th percentile of that hour's
5% depth over four weeks. The published number is the median run and the range is the least and most of the three.
The range is not ordered by book: a thinner book liquidates the same accounts at lower prices, which can mean fewer
dollars of selling.

Validation needs no history the tape did not already have. Sweeps before account margin was stored have liquidation
prices on every position, and the model falls back to them for a cross position without an account row, so episodes
since 31 October can be replayed now. An episode is a market falling 8% within 24 hours of a sweep; the replay
evaluates positions at the realized trough (the lowest fill in the window) and compares with liquidations confirmed
for the same address and market. Recorded liquidations are a floor (see Liquidations above), so the replay errs
towards predicting more than was recorded.

Publication has three gates, as the doc's plan says: complex coverage at 98%, the model validated (3+ recent episodes
with $250k+ recorded, 75% within 50%), and a person's wording review. The weekly job always sends the card to
Telegram, marked NOT FOR POSTING while a gate is open; `tape stress-release <date>` is the review's sign-off and
refuses while either automatic gate fails. The site serves `/stress/<date>`, its permalinks and its PNGs only for
released runs.

### Standing series

Computed in the hourly metrics job on the 00:00 UTC hour from daily closes (`candles`): backfilled once from
`candleSnapshot` by `tape candles`, then extended from each 00:00 UTC mark. Histories are short: on 2026-11-05 xyz:CRWV
had 283 daily candles, io:ANTH 79 and xyz:ANT 10. Baskets are equal-weight over the markets that have a return that
day, so the labs basket was mostly io:ANTH and io:OAI before xyz:ANT's candles start.

First values (UTC close of 2026-11-04, 124 days of history): labs and neoclouds 0.42 over 30 days (0.31 on sell-off
days), labs and chips 0.57 (0.55), labs and memory 0.57 (0.50). Each sits between the 40th and 51st percentile of its
own history, so nothing alerts.

Alerts fire when a series enters its extreme (95th percentile of its own history, after 60 days of it), at most once a
week per series, and only after the event warm-up. A deal-study alert fires when a deal the tape watched completes
its five-day window; old edges are studied but do not page.

### Not built

- **The prediction ledger.** The doc requires a passed backtest before any predictive claim and a public ledger that
  scores each claim at its horizon. Nothing in this build makes a predictive claim, so there is nothing to log yet.
- **GPU rental prices** on compute nodes: still an open question in the doc.
- **Paid data:** none, per the doc's non-goals.
