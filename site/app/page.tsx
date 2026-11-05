import type { Metadata } from "next";
import Link from "next/link";
import { Chart, Status } from "../components/Chart";
import { Cls, H2, MarketTable, Net } from "../components/Tables";
import { pct, usd } from "../lib/format";
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
      <Status hour={hour}>
        {sweep?.finished_at && <span>sweep of {sweep.addresses ?? 0} address-dex pairs</span>}
      </Status>
      <h1>Who is positioned which way in the AI complex on Hyperliquid</h1>
      {hour && (
        <p className="lede">
          Complex whale net <b>{pct(cx.whale_net?.value)}</b> of open interest, 24h{" "}
          <b>
            <Cls x={cx.whale_net_change?.value} />
          </b>
          . Coverage <b>{pct(cx.coverage?.value, 1, false)}</b>.
          {mover?.s.whale_net_change?.value != null && (
            <>
              {" "}
              Biggest mover: <Link href={`/m/${slug(mover.coin)}`}>{mover.coin}</Link>{" "}
              <b>
                <Cls x={mover.s.whale_net_change.value} />
              </b>
              .
            </>
          )}
        </p>
      )}
      {chartH && <Chart kind="home" subject="complex" hour={chartH} alt="Whale net positioning, AI complex" priority />}

      <H2 ja="部門">Legs</H2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>leg</th>
              <th className="n">whale net</th>
              <th className="n">24h</th>
              <th className="n">coverage</th>
              <th className="n">fresh</th>
              <th className="n">crowding</th>
              <th className="n">OI</th>
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
                  <td className="n">
                    <Net x={s.whale_net?.value} />
                  </td>
                  <td className="n">
                    <Cls x={s.whale_net_change?.value} />
                  </td>
                  <td className="n">{pct(s.coverage?.value, 1, false)}</td>
                  <td className="n">{pct(s.fresh_share?.value, 1, false)}</td>
                  <td className="n">{pct(s.crowding?.value, 1, false)}</td>
                  <td className="n">{usd(s.oi_usd?.value)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <H2 ja="銘柄">Markets, by 24h move</H2>
      <MarketTable rows={marketRows} />
    </>
  );
}
