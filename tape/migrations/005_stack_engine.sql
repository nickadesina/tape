-- Stack Engine (design doc, Nov 5 2026, revised): a sourced, bottom-up model
-- of the AI supply chain (layer 1), market-implied values for its nodes
-- (layer 2), and the reconciliation engine that measures the distance
-- (layer 3). The model's hand-checked inputs live in config/stack/; loaders
-- add filed and reported figures; the daily model run writes the rest.

-- Every document cited, by any quantity, relationship or forecast.
create table sources (
  id text primary key,                        -- a short key from config, or src_<sha1 of url>[:12]
  url text not null unique,
  title text,
  publisher text,
  published date,
  retrieved_at timestamptz not null default now()
);

-- Companies, facilities, products or SKUs, and themes.
create table entities (
  id text primary key,                        -- e.g. coreweave, h100, hbm
  name text not null,
  kind text not null,                         -- company | facility | sku | theme
  segment text not null,                      -- demand | accelerators | memory | packaging | power | cloud | economics | financing
  node text,                                  -- config/graph.yaml node, for companies
  markets text[] not null default '{}',       -- Hyperliquid markets that price it (from its graph node)
  ids jsonb not null default '{}',            -- cik, twse, ticker, ...
  note text
);

-- Numbers over time. One row per version of a figure: a revision closes the
-- current row (valid_to, revision_reason on the new row) and adds the next
-- version, so every change is kept and can be shown.
create table quantities (
  id bigserial primary key,
  key text not null,                          -- the series: <entity>.<metric>, e.g. coreweave.gpus_deployed
  entity text not null references entities(id),
  metric text not null,
  unit text not null,                         -- usd | usd_per_gpu_hour | gpus | units | gb | wafers_per_month | mw | ...
  period_start date not null,
  period_end date not null,
  value double precision not null,
  lo double precision not null,
  hi double precision not null,
  kind text not null,                         -- filed | company | reported | estimate
  method text,                                -- estimates: the formula (see tape/model/core.py METHODS)
  inputs text[] not null default '{}',        -- estimates: the keys it was computed from
  source text references sources(id),         -- null only for estimates
  origin text not null,                       -- config | edgar | taiwan | korea | model
  version int not null default 1,
  valid_from timestamptz not null default now(),
  valid_to timestamptz,                       -- null: the current version
  revision_reason text,                       -- why this version replaced the last one
  verified_at date,                           -- config rows: the last check against the source
  stale boolean not null default false,       -- not re-checked within stack.stale_days
  note text,                                  -- e.g. the XBRL concept, or how a quarter was derived
  check (lo <= value and value <= hi),
  unique (key, period_start, period_end, version)
);
create index quantities_current on quantities (key, period_end) where valid_to is null;
create index quantities_entity on quantities (entity) where valid_to is null;

-- The financing graph's edges with their history: `edges` stays the current
-- verified set the stress test and standing series read; this keeps every
-- version, closed by valid_to when the edge changes or leaves the file.
create table relationships (
  id bigserial primary key,
  edge text not null,
  src text not null,
  dst text not null,
  type text not null,
  size_usd double precision,
  status text,
  tenor text,
  share double precision,
  date date not null,
  source text not null,
  confidence text not null,
  verified_at date not null,
  valid_from timestamptz not null default now(),
  valid_to timestamptz
);
create index relationships_current on relationships (edge) where valid_to is null;

-- One row per daily model run (dry runs with drafts are never stored).
create table model_runs (
  id bigserial primary key,
  day date not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  ok boolean,
  config_hash text,                           -- sha1 of config/stack/*.yaml and graph.yaml
  quantities int,                             -- current quantities the run read
  estimates int,                              -- estimates it computed
  gaps int,
  flags int,
  note text
);

-- Layer 2 against layer 1, per node and day: the model's range, the
-- market-implied range, and the distance between them.
create table gaps (
  day date not null,
  node text not null,                         -- the comparison, from config/stack/conversions.yaml
  run_id bigint not null references model_runs(id) on delete cascade,
  entity text not null references entities(id),
  label text not null,
  metric text not null,
  unit text not null,
  model_value double precision not null,
  model_lo double precision not null,
  model_hi double precision not null,
  market_value double precision not null,
  market_lo double precision not null,
  market_hi double precision not null,
  distance double precision not null,         -- (market mid - model mid) / model mid
  overlap boolean not null,
  days_apart int not null,                    -- consecutive days without overlap, through this one
  flagged boolean not null,                   -- days_apart >= stack.gap_persist_days
  conversion text not null,                   -- the formula's id (tape/model/conversions.py)
  detail jsonb not null,                      -- formula, inputs with sources, assumptions, sensitivity
  primary key (day, node)
);
create index gaps_node on gaps (node, day);

-- Scenarios: one input changed and pushed through the quantity graph.
create table scenarios (
  id text not null,                           -- from config/stack/scenarios.yaml
  day date not null,
  run_id bigint not null references model_runs(id) on delete cascade,
  label text not null,
  shock jsonb not null,                       -- {key, change, note}
  path jsonb not null,                        -- [{key, label, unit, base: [lo, mid, hi], shocked: [lo, mid, hi], via}]
  revenue_at_risk jsonb not null,             -- [{entity, name, lo, mid, hi, via}]
  primary key (id, day)
);

-- Every published forecast, scored when the actual figure is reported.
create table forecasts (
  id text primary key,
  key text not null,                          -- the quantity series forecast
  entity text not null references entities(id),
  metric text not null,
  unit text not null,
  period_start date not null,
  period_end date not null,
  value double precision not null,
  lo double precision not null,
  hi double precision not null,
  published date not null,
  url text,                                   -- where it was published
  note text,
  actual double precision,
  actual_source text references sources(id),
  actual_kind text,
  error double precision,                     -- (value - actual) / actual
  within boolean,                             -- lo <= actual <= hi
  scored_at timestamptz
);

-- SF Compute's order book, hourly: best bid and ask per accelerator, SKU and
-- delivery window, in dollars per GPU-hour (node-hour / gpus_per_node).
create table rent_quotes (
  time timestamptz not null,                  -- collection time, to the minute
  accelerator text not null,                  -- h100 | h200 | b200 ...
  sku text not null,
  start_at timestamptz not null,
  end_at timestamptz not null,
  hours double precision not null,
  bid double precision,
  ask double precision,
  bid_nodes int,
  ask_nodes int,
  bids int,
  asks int,
  primary key (time, accelerator, sku, start_at, end_at)
);
create index rent_quotes_acc_time on rent_quotes (accelerator, time);

-- Executions, from /fills for each window the collector saw.
create table rent_fills (
  accelerator text not null,
  sku text not null,
  start_at timestamptz not null,
  end_at timestamptz not null,
  filled_at timestamptz not null,
  price double precision not null,            -- dollars per GPU-hour
  nodes int not null,
  primary key (accelerator, sku, start_at, end_at, filled_at, price, nodes)
);
create index rent_fills_acc_filled on rent_fills (accelerator, filled_at);
