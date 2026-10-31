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
