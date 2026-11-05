import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Chart } from "../../../components/Chart";
import { et, pct, usd } from "../../../lib/format";
import { stressRun } from "../../../lib/queries";
import { chartPng } from "../../../lib/urls";

// A published run never changes.
export const revalidate = 86400;

export async function generateStaticParams() {
  return [];
}

export async function generateMetadata(props: PageProps<"/stress/[date]">): Promise<Metadata> {
  const { date } = await props.params;
  const r = await stressRun(date);
  if (!r) return {};
  const url = chartPng("stress", "all", r.run.hour);
  const t = `Stress test ${date}`;
  return {
    title: t,
    openGraph: { title: t, images: [{ url, width: 1200, height: 675 }] },
    twitter: { images: [url] },
  };
}

function amp(x: number | null): string {
  return x == null ? "n/a" : `${x.toFixed(2)}×`;
}

export default async function StressPage(props: PageProps<"/stress/[date]">) {
  const { date } = await props.params;
  const r = await stressRun(date);
  if (!r) notFound();
  const { run, results } = r;
  const shocks = [...new Set(results.map((x) => x.shock))];
  return (
    <>
      <h1>Stress test, {date}</h1>
      <p className="dim">
        Fragility of the market&apos;s view, the monthly stress test. Positions from the {et(run.hour)} snapshot ({run.accounts ?? "?"} accounts, {run.positions ?? "?"} positions),
        order books from {run.book_hour ? et(run.book_hour) : "n/a"}. Complex coverage {pct(run.coverage, 1, false)}.
        Hyperliquid only: one venue, the transparent corner of the trade, not the whole market.
      </p>
      <Chart kind="stress" subject="all" hour={run.hour} alt={`Forced selling by scenario, ${date}`} priority />
      <Chart kind="stress" subject="top" hour={run.hour} alt={`The largest cascade, round by round, ${date}`} />
      {shocks.map((s) => (
        <section key={s}>
          <h2>{pct(s, 0)} shock</h2>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>scenario</th>
                  <th className="n">forced selling</th>
                  <th className="n">range</th>
                  <th className="n">accounts</th>
                  <th className="n">markets</th>
                  <th className="n">amplification</th>
                  <th className="n">rounds</th>
                  <th className="n">book ran out</th>
                </tr>
              </thead>
              <tbody>
                {results
                  .filter((x) => x.shock === s)
                  .map((x) => (
                    <tr key={x.scenario}>
                      <td>{x.label}</td>
                      <td className="n">{usd(x.forced_usd)}</td>
                      <td className="n">
                        {usd(x.forced_lo)} to {usd(x.forced_hi)}
                      </td>
                      <td className="n">{x.accounts_hit}</td>
                      <td className="n">{x.markets_hit}</td>
                      <td className="n">{amp(x.amplification)}</td>
                      <td className="n">{x.rounds}</td>
                      <td className="n">{x.unabsorbed_usd > 0 ? usd(x.unabsorbed_usd) : ""}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <p>
        The range runs each scenario on the thick, median and thin book for that hour over the last four weeks.
        Amplification is the shocked name&apos;s final drawdown over the initial shock. Accounts are counted, never
        named. <Link href="/method#stress">Method</Link>, <Link href="/map">the financing map</Link>.
      </p>
    </>
  );
}
