import type { Metadata } from "next";
import { Chart, Status } from "../../components/Chart";
import { H2, MarketTable } from "../../components/Tables";
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
      <Status hour={hour} />
      <h1>Whale net by leg</h1>
      <p className="lede">Same scale on every panel. Leg values are open-interest-weighted sums of their markets.</p>
      {chartH && <Chart kind="legs" subject="all" hour={chartH} alt="Whale net by leg, small multiples" priority />}
      {legList.map((l) => (
        <section key={l.key}>
          <H2 id={l.key}>{l.label}</H2>
          <MarketTable
            rows={marketList.filter((m) => m.leg === l.key).map((m) => ({ ...m, s: latest[m.coin] ?? {} }))}
          />
        </section>
      ))}
    </>
  );
}
