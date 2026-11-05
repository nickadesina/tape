import Link from "next/link";
import { amount, pct, period, ranged } from "../../lib/format";
import { calibration, forecasts } from "../../lib/queries";

export const revalidate = 300;
export const metadata = {
  title: "Forecast ledger",
  description: "Every forecast the stack model published, scored against the figure that was later reported.",
};

export default async function LedgerPage() {
  const [rows, cal] = await Promise.all([forecasts(), calibration()]);
  return (
    <>
      <h1>The forecast ledger</h1>
      <p>
        Every forecast published from the <Link href="/stack">stack model</Link>, with the range it was published with,
        kept here from the day it went out. When the actual figure is reported it is scored: the error of the mid, and
        whether the actual landed inside the range. Misses stay on the page.
      </p>

      <h2>Calibration</h2>
      {cal.scored === 0 ? (
        <p className="dim">
          {cal.forecasts === 0 ? "No forecast published yet." : `${cal.forecasts} published, none scored yet.`}
        </p>
      ) : (
        <table>
          <tbody>
            <tr>
              <td>published</td>
              <td className="n">{cal.forecasts}</td>
            </tr>
            <tr>
              <td>scored</td>
              <td className="n">{cal.scored}</td>
            </tr>
            <tr>
              <td>actual within the published range</td>
              <td className="n">{pct(cal.within_share, 0, false)}</td>
            </tr>
            <tr>
              <td>median absolute error of the mid</td>
              <td className="n">{pct(cal.median_abs_error, 1, false)}</td>
            </tr>
          </tbody>
        </table>
      )}

      <h2>Forecasts</h2>
      {rows.length === 0 ? (
        <p className="dim">None yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>key</th>
                <th>period</th>
                <th className="n">forecast [range]</th>
                <th>published</th>
                <th className="n">actual</th>
                <th className="n">error</th>
                <th>within</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id} id={f.id} title={f.note ?? undefined}>
                  <td>
                    <Link href={`/node/${f.entity}#${f.key}`}>{f.key}</Link>
                  </td>
                  <td>{period(f.period_start, f.period_end)}</td>
                  <td className="n">{ranged(f.value, f.lo, f.hi, f.unit)}</td>
                  <td>{f.url ? <a href={f.url}>{f.published}</a> : f.published}</td>
                  <td className="n">
                    {f.actual == null ? (
                      <span className="dim">pending</span>
                    ) : f.actual_url ? (
                      <a href={f.actual_url}>{amount(f.actual, f.unit)}</a>
                    ) : (
                      amount(f.actual, f.unit)
                    )}
                  </td>
                  <td className="n">{f.error == null ? "" : pct(f.error, 1)}</td>
                  <td className={f.within === false ? "neg" : undefined}>
                    {f.within == null ? "" : f.within ? "yes" : "no"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="dim">Error is (forecast mid − actual) / actual.</p>
    </>
  );
}
