-- Positioning Tape schema. Hours are UTC, truncated to the hour.
-- The design doc names four tables that matter (fills, snapshots, series,
-- events); the rest are bookkeeping that keeps those four honest.

create table legs (
  key text primary key,
  label text not null,
  ord int not null
);

create table markets (
  coin text primary key,              -- dex-prefixed, e.g. xyz:CRWV
  dex text not null,
  leg text not null references legs(key),
  max_leverage int,
  active boolean not null default true,
  updated_at timestamptz not null default now()
);

-- Every fill on a market in the universe. From the WebSocket both sides are
-- known; a fill backfilled from one address's userFillsByTime knows only that
-- side until the other side's backfill (or nothing) fills it in.
create table fills (
  coin text not null,
  tid bigint not null,
  time timestamptz not null,
  px double precision not null,
  sz double precision not null,
  side char(1),                       -- aggressor side: B (buy) or A (sell); null if unknown
  buyer text,
  seller text,
  hash text,
  source text not null default 'ws',  -- ws | backfill
  primary key (coin, tid)
);
create index fills_time on fills (time);
create index fills_buyer_time on fills (buyer, time);
create index fills_seller_time on fills (seller, time);

create table addresses (
  address text primary key,
  source text not null default 'ws',  -- ws | backfill | leaderboard
  first_seen timestamptz not null default now(),
  last_fill timestamptz,              -- last fill in the complex seen by the tape
  first_fill timestamptz,             -- earliest fill in the complex found in history
  first_fill_complete boolean,        -- true if history reached the address's first fill ever
  history_checked_at timestamptz
);

-- The sweep set is per (address, dex): clearinghouseState is per dex.
create table address_dexes (
  address text not null references addresses(address),
  dex text not null,
  last_fill timestamptz,
  last_position_hour timestamptz,     -- last sweep hour with a nonzero position on this dex
  first_swept_hour timestamptz,       -- positions on this dex are known from this hour on
  seed_pending boolean not null default false,
  primary key (address, dex)
);
create index address_dexes_seed on address_dexes (seed_pending) where seed_pending;

create table sweeps (
  hour timestamptz primary key,
  started_at timestamptz not null,
  finished_at timestamptz,
  addresses int,
  requests int,
  positions int,
  errors int
);

-- Exact open positions from clearinghouseState, nonzero only. An address in
-- the sweep at an hour (first_swept_hour <= hour) with no row here was flat.
create table snapshots (
  hour timestamptz not null,
  coin text not null,
  address text not null,
  size double precision not null,     -- signed, in coin units
  entry_px double precision,
  leverage double precision,
  liq_px double precision,
  primary key (hour, coin, address)
);
create index snapshots_address_hour on snapshots (address, hour);

-- Market context at the start of each sweep (metaAndAssetCtxs).
create table market_hours (
  coin text not null,
  hour timestamptz not null,
  oi double precision not null,       -- open interest, coin units, one side
  mark double precision not null,
  oracle double precision,
  funding double precision,           -- hourly rate as the API reports it
  premium double precision,           -- as the API reports it
  day_ntl_vlm double precision,
  primary key (coin, hour)
);

create table liquidations (
  coin text not null,
  tid bigint not null,
  address text not null,              -- the liquidated user
  time timestamptz not null,
  sz double precision not null,
  px double precision not null,
  notional double precision not null,
  method text,
  primary key (coin, tid, address)
);
create index liquidations_time on liquidations (time);

-- Daily cohort assignment (ET day). Whales are fixed for the day; mid and
-- small are recomputed hourly from gross notional and not stored here.
create table address_days (
  day date not null,
  address text not null,
  gross_usd double precision not null,
  fills_per_day double precision,
  median_hold_s double precision,
  is_bot boolean not null default false,
  whale_rank int,                     -- 1..N for whales, null otherwise
  primary key (day, address)
);
create index address_days_whales on address_days (day) where whale_rank is not null;

-- When the current same-sign position on a market started (entry age).
create table position_since (
  address text not null,
  coin text not null,
  sign smallint not null,
  since_hour timestamptz not null,
  primary key (address, coin)
);

-- Top addresses per market per hour, precomputed so the site only reads.
create table top_positions (
  coin text not null,
  hour timestamptz not null,
  rank int not null,
  address text not null,
  size double precision not null,
  notional double precision not null,
  cohort text not null,               -- whale | mid | small | bot
  fresh boolean not null,
  since_hour timestamptz,
  primary key (coin, hour, rank)
);

-- Every published number. subject is a coin (xyz:CRWV), a leg (leg:neoclouds)
-- or the whole complex (complex).
create table series (
  subject text not null,
  metric text not null,
  hour timestamptz not null,
  value double precision,
  coverage double precision,
  primary key (subject, metric, hour)
);
create index series_hour on series (hour);

-- One row per ET day from the daily snapshot hour, written only when the
-- subject's coverage clears the posting threshold.
create table daily_series (
  day date not null,
  subject text not null,
  metric text not null,
  hour timestamptz not null,
  value double precision,
  coverage double precision not null,
  primary key (day, subject, metric)
);

create table events (
  id bigserial primary key,
  time timestamptz not null,
  coin text not null,
  kind text not null,                 -- whale_flip | whale_entry | whale_net_move | funding | premium | liquidations
  address text,
  size_usd double precision,
  detail jsonb not null default '{}',
  dedupe_key text not null unique,
  chart_hour timestamptz,
  alerted_at timestamptz,
  post_url text,
  created_at timestamptz not null default now()
);
create index events_time on events (time desc);

create table charts (
  kind text not null,                 -- home | daily | legs | market | event
  subject text not null,
  hour timestamptz not null,
  content_type text not null default 'image/png',
  path text,                          -- object key when stored outside the database
  bytes bytea,                        -- the PNG when CHART_STORE=db
  width int,
  height int,
  created_at timestamptz not null default now(),
  primary key (kind, subject, hour)
);

create table runs (
  job text not null,
  key text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  ok boolean,
  note text,
  primary key (job, key)
);

-- Thresholds and cohort definitions the last metrics run used, for the method page.
create table definitions (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);
