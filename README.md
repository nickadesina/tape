# Positioning Tape

A daily chart of who is positioned which way in the AI complex on Hyperliquid, built from public chain state.
It has one Python process on the box, one Postgres, and one read-only Next.js site. Nothing in the pipeline posts to X;
the operator does.

```
Hyperliquid WS trades ─┐                      ┌─► charts (PNG) ─► site (Next.js on Vercel, read-only)
                       ├─► indexer ─► Postgres ┤
Hyperliquid info API ──┘   (asyncio)  (Neon)   └─► Telegram: 16:00 ET daily chart, event alerts ─► operator ─► X
```

The design doc (Oct 30, 2026) is the spec. `docs/BUILD_NOTES.md` records what the build found on chain and where it
differs from the doc.

**Stack Engine** (working title; design doc Nov 5, 2026, revised) is built on top of the tape. It keeps a sourced,
bottom-up model of the AI supply chain (`config/stack/`, plus loaders for SEC EDGAR, Taiwan monthly revenue, Korea
trade statistics and Ornn's GPU rent index), reconciles it with what markets price (Hyperliquid closes turned
into physical terms by documented conversions), and measures the gap. The model says what is physically possible,
the market says what is priced, and the engine measures the distance. Scenarios push one changed input through the
quantity graph; a forecast ledger scores every published forecast. The financing graph (`config/graph.yaml`) is one
part of the model, and the stress test is now a monthly exhibit, "fragility of the market's view". See "Stack Engine"
in `docs/BUILD_NOTES.md`.

## Layout

| Path | What |
|---|---|
| `config/universe.yaml` | The legs and their markets, candidates, and the names `tape match` recognizes. Checked against live `meta` on every start. |
| `config/tape.yaml` | Every threshold: cohorts, coverage, events, the daily hour, rate-limit budget. |
| `tape/indexer.py` | WebSocket `trades` for every market, the address set, the hourly `clearinghouseState` sweep, gap backfill, discovery and seed snapshots, first-fill history, liquidation confirmation, the alert sender. |
| `tape/hl.py` | Info client under the published weight limit, with high and low priority. |
| `tape/compute.py` | Every published number for one hour. Pure. |
| `tape/cohorts.py`, `tape/events.py` | Cohort rules, bot detection (FIFO median hold) and event rules. Pure. |
| `tape/metrics.py` | The metrics job: cohorts, series, top positions, events and daily rows. |
| `tape/render.py`, `tape/charts.py` | matplotlib charts (default look, Excel palette) and what feeds them. |
| `tape/daily.py`, `tape/alerts.py`, `tape/telegram.py` | The 16:00 ET job, event alerts, and Telegram. |
| `tape/match.py` | Maps a post's text to markets, so twitter-checker can attach the Tape chart. |
| `config/graph.yaml` | The financing graph: nodes (every market belongs to one) and sourced edges. Only edges a person verified load. |
| `tape/depth.py` | Book math: stitch the l2Book views, banded depth, walking a book. Pure. |
| `tape/stress.py` | The stress model: shock, margin, liquidation, book walk, rounds. Pure. |
| `tape/stressjob.py` | The weekly run, replay validation, the publication gates and release, the cards. |
| `tape/graph.py`, `tape/loop.py`, `tape/standing.py` | The graph, the three standing series (pure), and running them in the hourly metrics job. |
| `config/stack/` | The stack model's hand-checked part: entities, sources, figures and estimates, comparisons (conversions and their assumptions), scenario links, forecasts. Drafts load only once a person sets `verified_at` / `reviewed_at`. |
| `tape/model/` | The stack model and reconciliation engine, pure: ranges and estimates (`core`), conversions, gaps, scenarios, the forecast ledger, the leading-indicator bench. |
| `tape/modeljob.py` | The daily model run against Postgres: versions figures, computes estimates, gaps, scenarios, scores forecasts. |
| `tape/loaders/` | EDGAR, Taiwan, Korea and Ornn: figures straight from their source. Ornn's licence is internal use only, so everything derived from it is private: in `tape gaps` and Telegram, never on the site. |
| `tape/migrations/` | Schema. |
| `site/` | The public site: home, `/m/<market>`, `/legs`, `/events`, `/stress`, `/stress/<date>`, `/map`, `/stack`, `/node/<id>`, `/gaps`, `/scenarios/<id>`, `/ledger`, `/research/<slug>` (pieces in `site/content/research/`), `/method`, `/data`, permalinks `/p/...`, PNGs `/c/...`, CSV and JSON. |
| `deploy/` | systemd units and timers for the box, env example, install script. |

## Run it locally

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
export DATABASE_URL=postgresql://tape:tape@localhost/tape
tape migrate
tape universe                          # what is live, what is missing, what is unclassified
tape seed-leaderboard --min-account-value 1000000   # optional: see pre-existing big positions sooner
tape index                             # long-running; sweeps at the top of every hour, then runs metrics
tape status                            # sweeps, queues, coverage per market
tape daily --dry-run                   # render the daily chart without sending it
tape candles --days 400                # daily closes for the standing series (once; metrics keeps them up)
tape graph --drafts                    # check config/graph.yaml; list the edges awaiting verification
tape stress --dry-run                  # the stress test on the latest stress Sunday's sweep, without sending
SEC_USER_AGENT="<name> <email>" tape load   # EDGAR, Taiwan and Korea figures into the stack model
tape stack --drafts                    # check config/stack; list the figures awaiting verification
tape model --drafts                    # the model run with drafts treated as verified; stores nothing

cd site && npm install && DATABASE_URL=$DATABASE_URL npm run dev   # http://localhost:3000
```

Tests: `pytest` runs the pure suite. Set `DATABASE_URL_TEST` to a disposable database to also run the Postgres
suite, which wipes that database. `ruff check tape tests` and `ruff format --check` lint the code. CI runs all of
these, plus a typecheck and build of the site.

## Commands

| Command | |
|---|---|
| `tape index` | The indexer. Run it under systemd. |
| `tape metrics [--hour 2026-10-31T19Z]` | Compute every finished sweep without metrics, or recompute an hour; then render the hour's charts. |
| `tape render [--hour …]` | Re-render the site's charts. |
| `tape daily [--day YYYY-MM-DD] [--dry-run]` | The 16:00 ET job. If complex coverage is under 98%, it sends the gap-day line instead of a chart. |
| `tape alerts` | Send pending event alerts. The indexer does this every 20 s. |
| `tape event-posted <id> <url>` | Record the X post made from an alert; the events page links it. |
| `tape match "<post text>"` | JSON of the markets a post is about, with each market's latest chart and permalink. |
| `tape seed-leaderboard [--min-account-value N]` | Queue leaderboard addresses for a one-time low-priority snapshot. |
| `tape universe [--all] [--json]` | Resolve the configured complex against live meta. |
| `tape status` | Health at a glance, including depth collection and account margin. |
| `tape stress [--day YYYY-MM-DD] [--dry-run]` | The stress test (monthly exhibit): replay recent drawdowns, run every scenario, store, render, send the card. |
| `tape stress-replay [--days N]` | Replay recorded drawdowns against recorded liquidations; prints whether the model is validated. |
| `tape stress-release <date>` | Publish a stress run on the site after the wording review. Refuses while coverage or validation fails. |
| `tape graph [--drafts]` | Validate `config/graph.yaml`: nodes, verified, stale and draft edges, exposures. |
| `tape candles [--days N]` | Backfill daily closes from `candleSnapshot` for the standing series and the model's conversions. |
| `tape stack [--drafts]` | Validate `config/stack/`: what loads, which figures, judgments and comparisons are drafts. |
| `tape model [--day D] [--drafts] [--dry-run]` | The daily model run: estimates, gaps, scenarios, forecast scores. `--drafts` treats drafts as verified and stores nothing. |
| `tape load [edgar] [taiwan] [korea] [ornn]` | Run loaders (default: all). Each skips itself while its key is unset. |
| `tape gaps` | The latest gaps, private ones included: the operator's view. |

## Deploy

The tape runs on Vercel as two projects from this repo, both on Neon `tape-db` (Vercel Storage):

| Project | Root | What |
|---|---|---|
| `tape` | `site/` | The public site. |
| `tape-worker` | `.` | The pipeline as Vercel Cron functions (`vercel.json`, `api/*.py` → `tape/web.py` → `tape/worker.py`). |

Vercel functions stop after 800 s, so the worker cuts the always-on indexer into pieces:

- **`/api/shift`, every 10 minutes:** holds the WebSocket and runs the background loops for 11.5 minutes. Consecutive
  shifts overlap, so the trade feed always has a listener, and fills are keyed on `(coin, tid)`, so the overlap writes
  nothing twice. A shift starts from the last sweep's positions plus the fills since.
- **`/api/sweep`, every 15 minutes:** continues the hour's `clearinghouseState` sweep where the last call stopped
  (progress is committed per 200 pairs), then runs metrics and charts once the hour is done. It takes 75% of the info
  budget; a shift running alongside drops to the rest.
- **`/api/daily`, 16:00 ET:** two UTC schedules (20:00 and 21:00) for daylight time; only the one that lands on 16:00
  ET runs.
- **`/api/stress`, Sunday 16:30 ET:** the stress test, after the 15:00 ET sweep and before Asia opens. Two UTC
  schedules again; only the one that lands on 16:xx ET runs, and with `stress.cadence: monthly` only on the month's
  first Sunday.
- **`/api/model`, 01:30 UTC:** the stack model run, after the 00:00 UTC closes.
- **`/api/load`, 06:00 UTC:** EDGAR, Taiwan, Korea and Ornn loaders. Each writes only what is new, so release days
  need no calendar.
- **Depth:** each shift also holds three `l2Book` WebSockets (full precision, 3 and 2 significant figures) and writes
  banded depth every minute and a full book every hour. Overlapping shifts write each minute once.
- **`POST /api/task`** with `{"args": ["status"]}` runs any CLI command except `index`: `seed-leaderboard`, `metrics
  --hour …`, `event-posted …` and so on.

Every path but `/api/health` needs `Authorization: Bearer $CRON_SECRET`; Vercel Cron sends it. Worker environment:
`CRON_SECRET`, the Neon variables (the worker uses `DATABASE_URL_UNPOOLED`, since advisory locks and COPY need a
direct session), `SITE_URL`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; for the Stack Engine loaders,
`SEC_USER_AGENT` (SEC requires a declared name and contact) and `KOREA_DATA_KEY`.

```bash
curl -s -X POST -H "Authorization: Bearer $CRON_SECRET" -d '{"args":["status"]}' https://tape-worker-<team>.vercel.app/api/task
```

**Box, instead of the worker:** the same code runs as one long process. Clone, then `sudo deploy/install.sh`. It creates the `tape` user, installs into `/opt/tape`,
copies `deploy/tape.env.example` to `/etc/tape/tape.env` (fill it in and re-run), migrates, and enables:

- `tape-indexer.service`: always on. It launches the metrics job after every sweep.
- `tape-metrics.timer`: `:02` every hour, as a catch-up. An advisory lock keeps it from running alongside the
  post-sweep run.
- `tape-daily.timer`: 16:00 America/New_York, every day.
- `tape-stress.timer`: the first Sunday of the month, 16:30 America/New_York.
- `tape-model.timer` (01:30 UTC), `tape-load.timer` (06:00 UTC).

**Database:** `tape-db`, a Neon database created from Vercel Storage and attached to both projects, so Vercel sets
its connection variables and bills it. It is separate from OpenMarket's databases. The worker (or box) uses the direct
connection; the site uses the pooled one.

**Site (Vercel):** a new project with root directory `site/`. Environment:

| Var | |
|---|---|
| `DATABASE_URL` | Neon pooled connection string. |
| `SITE_URL` | `https://tape-nick-21c4.vercel.app` (or a custom domain added to the project). |
| `BASE_PATH` | Unset. Set it (e.g. `/tape`) only to serve the site under a path behind another site's rewrite. |
| `S3_PUBLIC_BASE` | Only with `CHART_STORE=s3` on the box. |

The site is standalone at `https://tape-nick-21c4.vercel.app`. A custom domain later is a domain added to the `tape`
project plus `SITE_URL` on both projects.

**Charts** default to `CHART_STORE=db`: PNG bytes in the `charts` table, pruned so a small Neon plan holds them.
Daily, event, and daily-hour charts are kept forever; other hourly cards are kept 30 days and market panels 2
days. To keep every hourly permalink forever, use `CHART_STORE=s3` with an R2 bucket (`pip install .[s3]`). It costs
cents a month.

## Daily routine

1. **16:00 ET:** the daily chart arrives in Telegram with the headline numbers and the permalink. If it is a gap day,
   the message says so and why.
2. **16:05 ET:** post the image with a hand-written take. Put the site link in the first reply.
3. **Alerts:** each one carries the market, size, address, explorer link, its chart and the count of event posts made
   today (cap 2). After posting, run `tape event-posted <id> <url>`.
4. **Quote-rides:** twitter-checker runs `tape match "<text>"` and attaches `charts[0].png` and `.permalink`.
5. **First Sunday of the month, ~16:30 ET:** the stress card arrives with the top three scenarios in the template ("A 20% drop in X forces
   $W of selling across N markets on Hyperliquid; amplification A×") and the gates. If both gates pass, review the
   wording, run `tape stress-release <date>`, and post before Asia opens. Until then the site does not show the run.
6. **Standing-series alerts** (one-asset regime, graph against market, deal studies) arrive like event alerts, each
   with its chart. Every post carries coverage and the one-venue caveat; nothing names a wallet or a person.
7. **Graph upkeep:** draft edges get checked against their source and given `verified_at`; corrections go under
   `corrections` in `config/graph.yaml`. `tape graph` flags stale edges.
8. **Stack upkeep:** `tape stack --drafts` lists figures, judgments and comparisons awaiting a person. Check each
   against its source and set `verified_at` (comparisons: review the assumptions, set `reviewed_at`). A changed figure
   needs a new `revision_reason`; the revision log on `/stack` is public. New persistent gaps arrive in Telegram.

## Costs

- **Box:** already running.
- **Neon:** the paid plan (about $20 a month). Minute depth and hourly account margin outgrow the free 0.5 GB within
  days.
- **Vercel:** hobby or the existing team.
- **R2:** about $0 to $1 a month, optional.
- **Domain:** $12 a year, later.
