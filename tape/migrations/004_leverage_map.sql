-- AI Leverage Map (design doc, Nov 5 2026): account margin, order-book depth,
-- the financing graph, and the stress test.

-- Per-position margin, from the same clearinghouseState the sweep already reads.
-- position_value / |size| is the mark the position was valued at, which the
-- stress test shocks from.
alter table snapshots
  add column margin_type text,                -- cross | isolated
  add column margin_used double precision,    -- for isolated: the position's margin plus its unrealized PnL
  add column position_value double precision; -- |size| * mark at the snapshot

-- Each swept account's margin summary per hour, per dex (HIP-3 margin is per
-- dex: a cross account on xyz shares nothing with one on io). Written in the
-- same chunk transaction as `snapshots`, for every pair with a position in the
-- complex. Totals include positions outside the complex.
create table accounts (
  hour timestamptz not null,
  address text not null,
  dex text not null,
  account_value double precision not null,    -- marginSummary.accountValue (cross and isolated)
  cross_value double precision,               -- crossMarginSummary.accountValue
  margin_used double precision,               -- marginSummary.totalMarginUsed
  cross_maintenance double precision,         -- crossMaintenanceMarginUsed
  ntl_pos double precision,                   -- marginSummary.totalNtlPos
  withdrawable double precision,
  margin_mode text not null,                  -- cross | isolated | mixed, over its complex positions
  primary key (hour, address, dex)
);
create index accounts_address_hour on accounts (address, hour);

-- Banded order-book depth every minute, from the l2Book WebSocket. USD of
-- resting size within 2%, 5% and 10% of mid on each side. reach_* is how far
-- the widest book view reached (a band past it is a floor, not a measure).
create table depth (
  coin text not null,
  time timestamptz not null,                  -- truncated to the minute
  mid double precision not null,
  bid2 double precision, bid5 double precision, bid10 double precision,
  ask2 double precision, ask5 double precision, ask10 double precision,
  reach_bid double precision,
  reach_ask double precision,
  primary key (coin, time)
);
create index depth_time on depth (time);

-- One full book per market per hour (the first sample in the hour), every
-- aggregation the collector subscribes to: {"full": [[bids], [asks]], "3": ..., "2": ...},
-- each level [px, sz]. The stress test walks these.
create table books (
  coin text not null,
  hour timestamptz not null,
  time timestamptz not null,
  mid double precision not null,
  views jsonb not null,
  primary key (coin, hour)
);

-- The financing graph, loaded from config/graph.yaml by the metrics job.
-- Only verified edges are loaded; drafts stay in the file.
create table nodes (
  key text primary key,
  name text not null,
  kind text not null,                         -- lab | neocloud | chip | memory | hyperscaler | lender | fund | other
  public boolean not null,
  markets text[] not null default '{}',
  note text
);

create table edges (
  id text primary key,
  src text not null references nodes(key),   -- direction follows the money
  dst text not null references nodes(key),
  type text not null,                         -- equity | compute_contract | supply | debt_lease | revenue_concentration | guarantee
  size_usd double precision,
  status text,                                -- announced | contracted
  tenor text,
  share double precision,                     -- revenue_concentration: share of dst's revenue from src
  date date not null,
  source text not null,
  confidence text not null,                   -- filed | company | reported
  note text,
  verified_at date not null,
  stale boolean not null default false        -- verified_at older than graph.stale_days
);

create table graph_corrections (
  id serial primary key,
  date date not null,
  edge text,
  change text not null,
  unique (date, edge, change)
);

-- Weekly stress test. One run per ET date, from that date's 15:00 ET sweep.
create table stress_runs (
  date date primary key,
  hour timestamptz not null,                  -- the sweep the positions come from
  book_hour timestamptz,                      -- the hour the order books come from
  coverage double precision,                  -- complex coverage at that sweep
  accounts int,
  positions int,
  gates jsonb not null default '{}',          -- coverage_ok, model_validated, and why
  released_at timestamptz,                    -- set by `tape stress-release`; the site shows only released runs
  created_at timestamptz not null default now()
);

create table stress_results (
  date date not null references stress_runs(date) on delete cascade,
  scenario text not null,                     -- e.g. node:xyz:CRWV@-20, graph:anthropic@-20
  label text not null,
  kind text not null,                         -- node | graph
  shock double precision not null,            -- e.g. -0.20
  forced_usd double precision not null,       -- central depth estimate
  forced_lo double precision,                 -- least of the thick, central and thin book runs
  forced_hi double precision,                 -- most of the three
  accounts_hit int not null,
  markets_hit int not null,
  amplification double precision,             -- final drawdown / initial shock, shocked market(s)
  rounds int not null,
  unabsorbed_usd double precision not null default 0,  -- forced flow beyond the book's reach
  detail jsonb not null,                      -- per market: price path, forced notional by round
  primary key (date, scenario)
);

-- Replays of past drawdowns: predicted forced selling against the
-- liquidations the tape recorded. The model is validated when enough recent
-- episodes land inside the tolerance.
create table stress_replays (
  start_hour timestamptz not null,
  end_hour timestamptz not null,
  coin text not null,                         -- the market whose drawdown defines the episode
  move double precision not null,             -- realized move of that market
  predicted_usd double precision not null,
  recorded_usd double precision not null,
  ratio double precision,                     -- predicted / recorded
  within boolean not null,
  detail jsonb not null default '{}',
  created_at timestamptz not null default now(),
  primary key (start_hour, coin)
);

-- Daily closes for the standing series (co-movement, graph against market,
-- deal studies). Backfilled from candleSnapshot by `tape candles`, then kept
-- up by the metrics job from each 00:00 UTC mark.
create table candles (
  coin text not null,
  day date not null,                          -- UTC day; close is the price at the end of it
  close double precision not null,
  source text not null default 'candle',      -- candle | mark
  primary key (coin, day)
);

-- Deal studies: both names' moves on Hyperliquid at a dated edge's
-- announcement and `loop.deal_days` later.
create table deal_studies (
  edge text not null,
  coin text not null,
  side text not null,                         -- src | dst
  day0 double precision,                      -- close on the day over close the day before
  day_n double precision,                     -- close n days later over close the day before
  circular boolean not null,
  computed_at timestamptz not null default now(),
  primary key (edge, coin)
);
