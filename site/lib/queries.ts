import { sql } from "./db";

// The site never computes anything: it reads series, snapshots and events
// the metrics job wrote, and renders them.

export type Leg = { key: string; label: string; ord: number };
export type Market = { coin: string; dex: string; leg: string };
export type Latest = Record<string, Record<string, { value: number | null; coverage: number | null }>>;

export const STALE_AFTER_MS = 2 * 3_600_000;

export async function latestHour(): Promise<Date | null> {
  const r = await sql`select max(hour) as h from series where subject = 'complex' and metric = 'whale_net'`;
  return (r[0]?.h as Date | null) ?? null;
}

export async function legs(): Promise<Leg[]> {
  const r = await sql`
    select l.key, l.label, l.ord from legs l
    where exists (select 1 from markets m where m.leg = l.key and m.active)
    order by l.ord`;
  return r as unknown as Leg[];
}

export async function markets(): Promise<Market[]> {
  const r = await sql`select coin, dex, leg from markets where active order by leg, coin`;
  return r as unknown as Market[];
}

export async function market(coin: string): Promise<(Market & { label: string; max_leverage: number }) | null> {
  const r = await sql`
    select m.coin, m.dex, m.leg, m.max_leverage, l.label from markets m join legs l on l.key = m.leg
    where m.coin = ${coin}`;
  return (r[0] as unknown as Market & { label: string; max_leverage: number }) ?? null;
}

export async function seriesAt(hour: Date, subjects?: string[]): Promise<Latest> {
  const r = subjects
    ? await sql`select subject, metric, value, coverage from series where hour = ${hour} and subject = any(${subjects})`
    : await sql`select subject, metric, value, coverage from series where hour = ${hour}`;
  const out: Latest = {};
  for (const row of r) {
    (out[row.subject] ??= {})[row.metric] = { value: row.value, coverage: row.coverage };
  }
  return out;
}

export async function chartHour(kind: string, subject: string, atOrBefore?: Date): Promise<Date | null> {
  const r = atOrBefore
    ? await sql`select max(hour) as h from charts where kind = ${kind} and subject = ${subject} and hour <= ${atOrBefore}`
    : await sql`select max(hour) as h from charts where kind = ${kind} and subject = ${subject}`;
  return (r[0]?.h as Date | null) ?? null;
}

export async function chart(kind: string, subject: string, hour: Date) {
  const r = await sql`
    select path, bytes, width, height, created_at from charts
    where kind = ${kind} and subject = ${subject} and hour = ${hour}`;
  return (r[0] as { path: string | null; bytes: Buffer | null; width: number; height: number } | undefined) ?? null;
}

export type TopRow = {
  rank: number;
  address: string;
  size: number;
  notional: number;
  cohort: string;
  fresh: boolean;
  since_hour: Date | null;
};

export async function topPositions(coin: string, hour: Date): Promise<TopRow[]> {
  const r = await sql`
    select rank, address, size, notional, cohort, fresh, since_hour from top_positions
    where coin = ${coin} and hour = ${hour} order by rank`;
  return r as unknown as TopRow[];
}

export type EventRow = {
  id: string;
  time: Date;
  coin: string;
  kind: string;
  address: string | null;
  size_usd: number | null;
  detail: Record<string, number | string | null>;
  post_url: string | null;
  event_chart_hour: Date | null;
};

export async function events(limit = 500, coin?: string): Promise<EventRow[]> {
  const r = coin
    ? await sql`
        select e.*, (select max(c.hour) from charts c where c.kind = 'event' and c.subject = e.id::text) as event_chart_hour
        from events e where e.coin = ${coin} and e.alerted_at is not null order by e.time desc limit ${limit}`
    : await sql`
        select e.*, (select max(c.hour) from charts c where c.kind = 'event' and c.subject = e.id::text) as event_chart_hour
        from events e where e.alerted_at is not null order by e.time desc limit ${limit}`;
  return r as unknown as EventRow[];
}

export async function eventChartHour(id: string): Promise<Date | null> {
  const r = await sql`select max(hour) as h from charts where kind = 'event' and subject = ${id}`;
  return (r[0]?.h as Date | null) ?? null;
}

export async function definitions(): Promise<Record<string, unknown>> {
  const r = await sql`select key, value from definitions`;
  return Object.fromEntries(r.map((x) => [x.key, x.value]));
}

export async function lastSweep(): Promise<{ hour: Date; finished_at: Date | null; addresses: number | null } | null> {
  const r = await sql`select hour, finished_at, addresses from sweeps where finished_at is not null order by hour desc limit 1`;
  return (r[0] as { hour: Date; finished_at: Date | null; addresses: number | null }) ?? null;
}

export async function metricNames(): Promise<string[]> {
  const r = await sql`select distinct metric from series where hour = (select max(hour) from series) order by metric`;
  return r.map((x) => x.metric as string);
}

export async function subjectNames(): Promise<string[]> {
  const r = await sql`
    select distinct subject from series where hour = (select max(hour) from series)
    order by subject`;
  return r.map((x) => x.subject as string);
}

export type SeriesPoint = { subject: string; metric: string; hour: Date; value: number | null; coverage: number | null };

export async function seriesRange(
  subjects: string[],
  metrics: string[],
  from: Date,
  to: Date,
  limit = 200_000,
): Promise<SeriesPoint[]> {
  const r = await sql`
    select subject, metric, hour, value, coverage from series
    where subject = any(${subjects}) and metric = any(${metrics}) and hour >= ${from} and hour <= ${to}
    order by hour, subject, metric limit ${limit}`;
  return r as unknown as SeriesPoint[];
}

export type DailyPoint = { day: string; subject: string; metric: string; hour: Date; value: number | null; coverage: number };

export async function dailyRange(subjects: string[] | null, metrics: string[] | null, from: string, to: string) {
  const r = await sql`
    select to_char(day, 'YYYY-MM-DD') as day, subject, metric, hour, value, coverage from daily_series
    where day >= ${from}::date and day <= ${to}::date
      and (${subjects}::text[] is null or subject = any(${subjects}::text[]))
      and (${metrics}::text[] is null or metric = any(${metrics}::text[]))
    order by day, subject, metric`;
  return r as unknown as DailyPoint[];
}

// ---- AI Leverage Map: the financing graph, the standing series, the stress test ----

export type GraphNode = { key: string; name: string; kind: string; public: boolean; markets: string[]; note: string | null };
export type GraphEdge = {
  id: string;
  src: string;
  dst: string;
  type: string;
  size_usd: number | null;
  status: string | null;
  tenor: string | null;
  share: number | null;
  date: string;
  source: string;
  confidence: string;
  note: string | null;
  verified_at: string;
  stale: boolean;
};

export async function graphNodes(): Promise<GraphNode[]> {
  const r = await sql`select key, name, kind, public, markets, note from nodes order by kind, name`;
  return r as unknown as GraphNode[];
}

export async function graphEdges(): Promise<GraphEdge[]> {
  const r = await sql`
    select id, src, dst, type, size_usd, status, tenor, share, to_char(date, 'YYYY-MM-DD') as date, source,
           confidence, note, to_char(verified_at, 'YYYY-MM-DD') as verified_at, stale
    from edges order by date desc, id`;
  return r as unknown as GraphEdge[];
}

export async function graphCorrections(): Promise<{ date: string; edge: string | null; change: string }[]> {
  const r = await sql`select to_char(date, 'YYYY-MM-DD') as date, edge, change from graph_corrections order by date desc`;
  return r as unknown as { date: string; edge: string | null; change: string }[];
}

export type StandingRow = { subject: string; metric: string; value: number | null; hour: Date };

/** The latest day's standing-series rows (regime:* and edge:* subjects). */
export async function standingLatest(): Promise<StandingRow[]> {
  const r = await sql`
    select subject, metric, value, hour from series
    where hour = (select max(hour) from series where subject like 'regime:%')
      and (subject like 'regime:%' or subject like 'edge:%')
    order by subject, metric`;
  return r as unknown as StandingRow[];
}

export type DealRow = {
  edge: string;
  src: string;
  dst: string;
  date: string;
  coin: string;
  side: string;
  day0: number | null;
  day_n: number | null;
  circular: boolean;
};

export async function dealStudies(): Promise<DealRow[]> {
  const r = await sql`
    select d.edge, e.src, e.dst, to_char(e.date, 'YYYY-MM-DD') as date, d.coin, d.side, d.day0, d.day_n, d.circular
    from deal_studies d join edges e on e.id = d.edge order by e.date desc, d.edge, d.side desc, d.coin`;
  return r as unknown as DealRow[];
}

export type DealQuarter = { quarter: string; deals: number; day0: number | null; day_n: number | null };

/** Circular deals by quarter: the receiving side's average move at announcement and n days later. */
export async function dealQuarters(): Promise<DealQuarter[]> {
  const r = await sql`
    select to_char(e.date, 'YYYY"Q"Q') as quarter, count(distinct d.edge)::int as deals,
           avg(d.day0) as day0, avg(d.day_n) as day_n
    from deal_studies d join edges e on e.id = d.edge
    where d.side = 'dst' and d.circular
    group by 1 order by 1 desc`;
  return r as unknown as DealQuarter[];
}

export type StressRun = {
  date: string;
  hour: Date;
  book_hour: Date | null;
  coverage: number | null;
  accounts: number | null;
  positions: number | null;
  gates: Record<string, unknown>;
  released_at: Date | null;
};

export type StressResult = {
  scenario: string;
  label: string;
  kind: string;
  shock: number;
  forced_usd: number;
  forced_lo: number | null;
  forced_hi: number | null;
  accounts_hit: number;
  markets_hit: number;
  amplification: number | null;
  rounds: number;
  unabsorbed_usd: number;
  detail: { markets: Record<string, { drawdown: number | null; forced_usd: number[] }> };
};

/** Released runs only: a run is public once its gates pass and a person has released it. */
export async function stressRuns(): Promise<StressRun[]> {
  const r = await sql`
    select to_char(date, 'YYYY-MM-DD') as date, hour, book_hour, coverage, accounts, positions, gates, released_at
    from stress_runs where released_at is not null order by date desc`;
  return r as unknown as StressRun[];
}

export async function stressRun(date: string): Promise<{ run: StressRun; results: StressResult[] } | null> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const r = await sql`
    select to_char(date, 'YYYY-MM-DD') as date, hour, book_hour, coverage, accounts, positions, gates, released_at
    from stress_runs where date = ${date}::date and released_at is not null`;
  if (!r[0]) return null;
  const results = await sql`
    select scenario, label, kind, shock, forced_usd, forced_lo, forced_hi, accounts_hit, markets_hit, amplification,
           rounds, unabsorbed_usd, detail
    from stress_results where date = ${date}::date order by shock desc, forced_usd desc`;
  return { run: r[0] as unknown as StressRun, results: results as unknown as StressResult[] };
}

/** Stress charts are public only once their run is released. */
export async function stressReleased(hour: Date): Promise<boolean> {
  const r = await sql`select 1 from stress_runs where hour = ${hour} and released_at is not null limit 1`;
  return r.length > 0;
}

export async function latestReleasedStressHour(): Promise<Date | null> {
  const r = await sql`select max(hour) as h from stress_runs where released_at is not null`;
  return (r[0]?.h as Date | null) ?? null;
}

// ---- Stack Engine: the stack model, gaps, scenarios, the forecast ledger ----

export const SEGMENTS: { key: string; label: string }[] = [
  { key: "demand", label: "Demand" },
  { key: "accelerators", label: "Accelerators" },
  { key: "memory", label: "Memory" },
  { key: "packaging", label: "Packaging and foundry" },
  { key: "power", label: "Power and data centres" },
  { key: "cloud", label: "Cloud capacity and price" },
  { key: "economics", label: "Economics" },
  { key: "financing", label: "Financing" },
];

export type Entity = {
  id: string;
  name: string;
  kind: string;
  segment: string;
  node: string | null;
  markets: string[];
  ids: Record<string, string | number>;
  note: string | null;
};

export type Quantity = {
  id: string;
  key: string;
  entity: string;
  metric: string;
  unit: string;
  period_start: string;
  period_end: string;
  value: number;
  lo: number;
  hi: number;
  kind: string; // filed | company | reported | estimate
  method: string | null;
  inputs: { key: string; entity: string | null }[];
  source: string | null;
  source_url: string | null;
  source_title: string | null;
  source_publisher: string | null;
  origin: string;
  version: number;
  valid_from: Date;
  valid_to: Date | null;
  revision_reason: string | null;
  verified_at: string | null;
  stale: boolean;
};

const ENTITY_ORDER = SEGMENTS.map((s) => s.key);

export async function entities(): Promise<Entity[]> {
  const r = await sql`
    select id, name, kind, segment, node, markets, ids, note from entities
    order by array_position(${ENTITY_ORDER}::text[], segment) nulls last, segment, name`;
  return r as unknown as Entity[];
}

export async function entity(id: string): Promise<Entity | null> {
  const r = await sql`select id, name, kind, segment, node, markets, ids, note from entities where id = ${id}`;
  return (r[0] as unknown as Entity) ?? null;
}

// Rows marked private (computed from a source licensed for internal use only, such as Ornn's GPU index)
// never reach the site: every query on quantities, gaps and scenarios filters them out.

// Each input key with the entity that carries it, so an estimate can link what it was computed from.
const quantityColumns = () => sql`
  q.id::text as id, q.key, q.entity, q.metric, q.unit,
  to_char(q.period_start, 'YYYY-MM-DD') as period_start, to_char(q.period_end, 'YYYY-MM-DD') as period_end,
  q.value, q.lo, q.hi, q.kind, q.method,
  coalesce((select jsonb_agg(jsonb_build_object(
              'key', k, 'entity', (select i.entity from quantities i where i.key = k limit 1)) order by n)
            from unnest(q.inputs) with ordinality as u(k, n)), '[]'::jsonb) as inputs,
  q.source, s.url as source_url, s.title as source_title, s.publisher as source_publisher,
  q.origin, q.version, q.valid_from, q.valid_to, q.revision_reason,
  to_char(q.verified_at, 'YYYY-MM-DD') as verified_at, q.stale`;

/** Current quantities (valid_to is null), the latest period per key; all entities or one. */
export async function currentQuantities(entityId?: string): Promise<Quantity[]> {
  const r = await sql`
    select * from (
      select distinct on (q.key) ${quantityColumns()}
      from quantities q left join sources s on s.id = q.source
      where q.valid_to is null and not q.private ${entityId ? sql`and q.entity = ${entityId}` : sql``}
      order by q.key, q.period_end desc, q.period_start desc
    ) x order by entity, key`;
  return r as unknown as Quantity[];
}

/** Every version of every figure for one entity: by key, newest period and version first. */
export async function quantityHistory(entityId: string): Promise<Quantity[]> {
  const r = await sql`
    select ${quantityColumns()}
    from quantities q left join sources s on s.id = q.source
    where q.entity = ${entityId} and not q.private
    order by q.key, q.period_end desc, q.period_start desc, q.version desc`;
  return r as unknown as Quantity[];
}

export type Revision = {
  key: string;
  entity: string;
  unit: string;
  period_start: string;
  period_end: string;
  version: number;
  old_value: number | null;
  old_lo: number | null;
  old_hi: number | null;
  value: number;
  lo: number;
  hi: number;
  revision_reason: string | null;
  valid_from: Date;
};

/** The public revision log: every version after the first, against the one it replaced. */
export async function quantityRevisions(limit = 1000): Promise<Revision[]> {
  const r = await sql`
    select q.key, q.entity, q.unit, to_char(q.period_start, 'YYYY-MM-DD') as period_start,
           to_char(q.period_end, 'YYYY-MM-DD') as period_end, q.version,
           p.value as old_value, p.lo as old_lo, p.hi as old_hi, q.value, q.lo, q.hi,
           q.revision_reason, q.valid_from
    from quantities q
    left join quantities p on p.key = q.key and p.period_start = q.period_start
      and p.period_end = q.period_end and p.version = q.version - 1
    where q.version > 1 and not q.private
    order by q.valid_from desc, q.key limit ${limit}`;
  return r as unknown as Revision[];
}

export type GapInput = {
  name: string;
  key?: string;
  value: number;
  lo: number;
  hi: number;
  unit: string;
  kind?: string;
  source?: string;
};
export type GapAssumption = { name: string; value: number; lo: number; hi: number; unit: string; note?: string };
export type GapDetail = {
  formula?: string;
  inputs?: GapInput[];
  assumptions?: GapAssumption[];
  sensitivity?: { name: string; low: number; high: number }[];
  model_from?: string[];
  caveat?: string;
};

export type Gap = {
  day: string;
  node: string;
  entity: string;
  label: string;
  metric: string;
  unit: string;
  model_value: number;
  model_lo: number;
  model_hi: number;
  market_value: number;
  market_lo: number;
  market_hi: number;
  distance: number;
  overlap: boolean;
  days_apart: number;
  flagged: boolean;
  conversion: string;
  detail: GapDetail;
};

export type GapPoint = Omit<Gap, "detail" | "conversion" | "metric">;

const gapColumns = () => sql`
  to_char(day, 'YYYY-MM-DD') as day, node, entity, label, unit, model_value, model_lo, model_hi,
  market_value, market_lo, market_hi, distance, overlap, days_apart, flagged`;

/** The latest day's gaps, one per node: flagged first, then by distance. */
export async function latestGaps(): Promise<Gap[]> {
  const r = await sql`
    select ${gapColumns()}, metric, conversion, detail from gaps
    where not private and day = (select max(day) from gaps where not private)
    order by flagged desc, abs(distance) desc, node`;
  return r as unknown as Gap[];
}

/** Each node's daily gaps over the last `days` days up to the latest day, oldest first. */
export async function gapHistory(days = 90, entityId?: string): Promise<GapPoint[]> {
  const r = await sql`
    select ${gapColumns()} from gaps
    where not private and day > (select max(day) from gaps where not private) - ${days}::int
      ${entityId ? sql`and entity = ${entityId}` : sql``}
    order by node, day`;
  return r as unknown as GapPoint[];
}

/** The latest gap row for each node of an entity (with its detail). */
export async function entityLatestGaps(entityId: string): Promise<Gap[]> {
  const r = await sql`
    select distinct on (node) ${gapColumns()}, metric, conversion, detail from gaps
    where entity = ${entityId} and not private order by node, day desc`;
  return r as unknown as Gap[];
}

export type ModelRun = {
  id: string;
  day: string;
  started_at: Date;
  finished_at: Date | null;
  ok: boolean | null;
  quantities: number | null;
  estimates: number | null;
  gaps: number | null;
  flags: number | null;
  note: string | null;
};

export async function latestModelRun(): Promise<ModelRun | null> {
  const r = await sql`
    select id::text as id, to_char(day, 'YYYY-MM-DD') as day, started_at, finished_at, ok, quantities, estimates,
           gaps, flags, note
    from model_runs order by day desc, started_at desc limit 1`;
  return (r[0] as unknown as ModelRun) ?? null;
}

export type EntityEdge = GraphEdge & { src_name: string; dst_name: string };

/** Financing-graph edges into or out of one graph node. */
export async function nodeEdges(node: string): Promise<EntityEdge[]> {
  const r = await sql`
    select e.id, e.src, e.dst, a.name as src_name, b.name as dst_name, e.type, e.size_usd, e.status, e.tenor,
           e.share, to_char(e.date, 'YYYY-MM-DD') as date, e.source, e.confidence, e.note,
           to_char(e.verified_at, 'YYYY-MM-DD') as verified_at, e.stale
    from edges e join nodes a on a.key = e.src join nodes b on b.key = e.dst
    where e.src = ${node} or e.dst = ${node}
    order by e.date desc, e.id`;
  return r as unknown as EntityEdge[];
}

export type ScenarioSummary = { id: string; label: string; day: string; days: number };

export async function scenarioList(): Promise<ScenarioSummary[]> {
  const r = await sql`
    select distinct on (id) id, label, to_char(day, 'YYYY-MM-DD') as day,
           (count(*) over (partition by id))::int as days
    from scenarios where not private order by id, day desc`;
  return r as unknown as ScenarioSummary[];
}

export type Triple = [number, number, number];
export type Scenario = {
  id: string;
  day: string;
  label: string;
  shock: { key: string; change: number | string; note?: string };
  path: { key: string; label: string; unit: string; base: Triple; shocked: Triple; via?: string }[];
  revenue_at_risk: { entity: string; name: string; lo: number; mid: number; hi: number; via?: string }[];
};

/** One scenario on a day, or on its latest day; with every day it was run. */
export async function scenario(id: string, day?: string): Promise<{ scenario: Scenario; days: string[] } | null> {
  if (day && !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const r = await sql`
    select id, to_char(day, 'YYYY-MM-DD') as day, label, shock, path, revenue_at_risk from scenarios
    where id = ${id} and not private ${day ? sql`and day = ${day}::date` : sql``}
    order by day desc limit 1`;
  if (!r[0]) return null;
  const d = await sql`
    select to_char(day, 'YYYY-MM-DD') as day from scenarios where id = ${id} and not private order by day desc`;
  return { scenario: r[0] as unknown as Scenario, days: d.map((x) => x.day as string) };
}

export type Forecast = {
  id: string;
  key: string;
  entity: string;
  metric: string;
  unit: string;
  period_start: string;
  period_end: string;
  value: number;
  lo: number;
  hi: number;
  published: string;
  url: string | null;
  note: string | null;
  actual: number | null;
  actual_source: string | null;
  actual_url: string | null;
  actual_kind: string | null;
  error: number | null;
  within: boolean | null;
  scored_at: Date | null;
};

export async function forecasts(): Promise<Forecast[]> {
  const r = await sql`
    select f.id, f.key, f.entity, f.metric, f.unit, to_char(f.period_start, 'YYYY-MM-DD') as period_start,
           to_char(f.period_end, 'YYYY-MM-DD') as period_end, f.value, f.lo, f.hi,
           to_char(f.published, 'YYYY-MM-DD') as published, f.url, f.note, f.actual, f.actual_source,
           s.url as actual_url, f.actual_kind, f.error, f.within, f.scored_at
    from forecasts f left join sources s on s.id = f.actual_source
    order by f.published desc, f.id`;
  return r as unknown as Forecast[];
}

export type Calibration = {
  forecasts: number;
  scored: number;
  within_share: number | null;
  median_abs_error: number | null;
};

/** Calibration over scored forecasts: how many, the share whose actual landed in range, the median |error|. */
export async function calibration(): Promise<Calibration> {
  const r = await sql`
    select count(*)::int as forecasts,
           (count(*) filter (where actual is not null))::int as scored,
           avg(case when within then 1.0 else 0.0 end) filter (where actual is not null and within is not null)
             ::double precision as within_share,
           (percentile_cont(0.5) within group (order by abs(error)) filter (where actual is not null and error is not null))
             ::double precision as median_abs_error
    from forecasts`;
  return r[0] as unknown as Calibration;
}
