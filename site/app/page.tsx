import type { Metadata } from "next";
import Link from "next/link";
import { Chart, Stale } from "../components/Chart";
import { Cls, MarketTable } from "../components/Tables";
import { pct, usd, utc } from "../lib/format";
import { chartHour, lastSweep, latestHour, legs, markets, seriesAt } from "../lib/queries";
import { chartPng, slug } from "../lib/urls";

export const revalidate = 300;

export async function generateMetadata(): Promise<Metadata> {
  const h = await chartHour("home", "complex");
  const images = h ? [{ url: chartPng("home", "complex", h), width: 1200, height: 675 }] : [];
  return {
    title: { absolute: "Positioning Tape: who is positioned which way in the AI complex" },
    openGraph: { title: "Positioning Tape", images },
    twitter: { images: images.map((i) => i.url) },
  };
}

export default async function Home() {
  const [hour, chartH, sweep, legList, marketList] = await Promise.all([
    latestHour(),
    chartHour("home", "complex"),
    lastSweep(),
    legs(),
    markets(),
  ]);
  const latest = hour ? await seriesAt(hour) : {};
  const cx = latest.complex ?? {};
  const marketRows = marketList
    .map((m) => ({ ...m, s: latest[m.coin] ?? {} }))
    .filter((m) => m.s.whale_net)
    .sort((a, b) => Math.abs(b.s.whale_net_change?.value ?? 0) - Math.abs(a.s.whale_net_change?.value ?? 0));
  const mover = marketRows[0];

  return (
    <>
      <h1>Who is positioned which way in the AI complex on Hyperliquid</h1>
      <Stale hour={hour} />
      {hour && (
        <p>
          Complex whale net <b>{pct(cx.whale_net?.value)}</b> of open interest, 24h <Cls x={cx.whale_net_change?.value} />
          . Coverage {pct(cx.coverage?.value, 1, false)}. As of {utc(hour)}
          {sweep?.finished_at ? `, sweep of ${sweep.addresses ?? 0} address-dex pairs.` : "."}
          {mover?.s.whale_net_change?.value != null && (
            <>
              {" "}
              Biggest mover: <Link href={`/m/${slug(mover.coin)}`}>{mover.coin}</Link>{" "}
              <Cls x={mover.s.whale_net_change.value} />.
            </>
          )}
        </p>
      )}
      {chartH && <Chart kind="home" subject="complex" hour={chartH} alt="Whale net positioning, AI complex" priority />}

      <h2>Legs</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>leg</th>
              <th className="n">whale net</th>
              <th className="n">24h</th>
              <th className="n">fresh</th>
              <th className="n">crowding</th>
              <th className="n">OI</th>
              <th className="n">coverage</th>
            </tr>
          </thead>
          <tbody>
            {legList.map((l) => {
              const s = latest[`leg:${l.key}`] ?? {};
              return (
                <tr key={l.key}>
                  <td>
                    <Link href={`/legs#${l.key}`}>{l.label}</Link>
                  </td>
                  <td className="n">{pct(s.whale_net?.value)}</td>
                  <td className="n">
                    <Cls x={s.whale_net_change?.value} />
                  </td>
                  <td className="n">{pct(s.fresh_share?.value, 1, false)}</td>
                  <td className="n">{pct(s.crowding?.value, 1, false)}</td>
                  <td className="n">{usd(s.oi_usd?.value)}</td>
                  <td className="n">{pct(s.coverage?.value, 1, false)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h2>Markets, by 24h move</h2>
      <MarketTable rows={marketRows} />
    </>
  );
}
