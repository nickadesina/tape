import type { Metadata } from "next";
import { Chart, Stale } from "../../components/Chart";
import { MarketTable } from "../../components/Tables";
import { chartHour, latestHour, legs, markets, seriesAt } from "../../lib/queries";
import { chartPng } from "../../lib/urls";

export const revalidate = 300;

export async function generateMetadata(): Promise<Metadata> {
  const h = await chartHour("legs", "all");
  const images = h ? [{ url: chartPng("legs", "all", h), width: 1200, height: 675 }] : [];
  return { title: "Legs", openGraph: { title: "Whale net by leg", images }, twitter: { images: images.map((i) => i.url) } };
}

export default async function Legs() {
  const [hour, chartH, legList, marketList] = await Promise.all([
    latestHour(),
    chartHour("legs", "all"),
    legs(),
    markets(),
  ]);
  const latest = hour ? await seriesAt(hour) : {};
  return (
    <>
      <h1>Whale net by leg</h1>
      <Stale hour={hour} />
      <p>Same scale on every panel. Leg values are open-interest-weighted sums of their markets.</p>
      {chartH && <Chart kind="legs" subject="all" hour={chartH} alt="Whale net by leg, small multiples" priority />}
      {legList.map((l) => (
        <section key={l.key}>
          <h2 id={l.key}>{l.label}</h2>
          <MarketTable
            rows={marketList.filter((m) => m.leg === l.key).map((m) => ({ ...m, s: latest[m.coin] ?? {} }))}
          />
        </section>
      ))}
    </>
  );
}
