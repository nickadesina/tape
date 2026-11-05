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
