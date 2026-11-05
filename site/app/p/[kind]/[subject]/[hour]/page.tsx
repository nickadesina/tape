import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Chart } from "../../../../../components/Chart";
import { et, utc } from "../../../../../lib/format";
import { chart, stressReleased } from "../../../../../lib/queries";
import { chartPng, parseHourKey, slug, unslug } from "../../../../../lib/urls";

// A permalink pins the hour: the chart quoted in an argument stays the chart it was.
export const revalidate = 86400;

// No paths at build time; each is rendered on first request and then cached.
export async function generateStaticParams() {
  return [];
}

const KINDS = new Set(["home", "daily", "legs", "market", "card", "event", "stress"]);

async function resolve(props: PageProps<"/p/[kind]/[subject]/[hour]">) {
  const { kind, subject, hour } = await props.params;
  const h = parseHourKey(hour);
  if (!KINDS.has(kind) || !h) return null;
  if (kind === "stress" && !(await stressReleased(h))) return null;
  const subj = unslug(decodeURIComponent(subject));
  const c = await chart(kind, subj, h);
  return c ? { kind, subject: subj, hour: h, chart: c } : null;
}

function title(kind: string, subject: string): string {
  if (kind === "home" || kind === "daily") return "Whale net positioning, AI complex";
  if (kind === "legs") return "Whale net by leg";
  if (kind === "event") return `Event ${subject}`;
  if (kind === "stress") return subject === "top" ? "Stress test: the largest cascade" : "Stress test: forced selling by scenario";
  return `${subject.replace("@", ", ")} positioning`;
}

export async function generateMetadata(props: PageProps<"/p/[kind]/[subject]/[hour]">): Promise<Metadata> {
  const r = await resolve(props);
  if (!r) return {};
  const url = chartPng(r.kind, r.subject, r.hour);
  const t = `${title(r.kind, r.subject)} · ${utc(r.hour)}`;
  return {
    title: t,
    openGraph: { title: t, images: [{ url, width: r.chart.width, height: r.chart.height }] },
    twitter: { images: [url] },
  };
}

export default async function Permalink(props: PageProps<"/p/[kind]/[subject]/[hour]">) {
  const r = await resolve(props);
  if (!r) notFound();
  const market = r.kind === "card" || r.kind === "market" ? r.subject.split("@")[0] : null;
  return (
    <>
      <h1>{title(r.kind, r.subject)}</h1>
      <p className="dim">
        Pinned to {utc(r.hour)} ({et(r.hour)}). This URL always shows this hour.
      </p>
      <Chart
        kind={r.kind}
        subject={r.subject}
        hour={r.hour}
        alt={title(r.kind, r.subject)}
        width={r.chart.width}
        height={r.chart.height}
        priority
      />
      <p>
        {market ? <Link href={`/m/${slug(market)}`}>{market} now</Link> : <Link href="/">now</Link>}
      </p>
    </>
  );
}
