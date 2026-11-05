import Link from "next/link";
import { GapChart, GapDetailView } from "../../components/Stack";
import { pct, ranged, unitLabel, utc } from "../../lib/format";
import { gapHistory, latestGaps, latestModelRun } from "../../lib/queries";

export const revalidate = 300;
export const metadata = {
  title: "Gaps",
  description:
    "Where what the AI supply chain can physically deliver and what the market prices it at disagree, measured daily.",
};

export default async function GapsPage() {
  const [gaps, points, run] = await Promise.all([latestGaps(), gapHistory(90), latestModelRun()]);
  const day = gaps[0]?.day;
  return (
    <>
      <h1>Gaps</h1>
      <p>
        The <Link href="/stack">stack model</Link> says what is physically possible; the market says what is priced.
        For each node below, the model&apos;s range and the range the market&apos;s prices imply, converted into the
        same unit, and the distance between their mids. A gap is flagged when the two ranges have not overlapped for a
        set number of days in a row. Every conversion prints its formula and assumptions. The{" "}
        <Link href="/method#stack">method</Link> says how.
      </p>
      <p className="dim">
        {run ? (
          <>
            Latest model run: {run.day}, {run.ok == null ? "unfinished" : run.ok ? "ok" : "failed"}
            {run.finished_at ? ` (finished ${utc(run.finished_at)})` : ""}
            {run.note ? `. ${run.note}` : ""}
          </>
        ) : (
          "No model run yet."
        )}
      </p>

      {gaps.length === 0 ? (
        <p className="dim">
          No gaps yet. They are computed daily once the model&apos;s figures have been verified against their sources.
        </p>
      ) : (
        <>
          <h2>{day}</h2>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>node</th>
                  <th>unit</th>
                  <th className="n">model range</th>
                  <th className="n">market range</th>
                  <th className="n">distance</th>
                  <th>overlap</th>
                  <th className="n">days apart</th>
                  <th>flagged</th>
                </tr>
              </thead>
              <tbody>
                {gaps.map((g) => (
                  <tr key={g.node} className={g.flagged ? "neg" : undefined}>
                    <td>
                      <Link href={`/node/${g.entity}#gap-${g.node}`}>{g.label}</Link>{" "}
                      <a href={`#${g.node}`} className="dim">
                        detail
                      </a>
                    </td>
                    <td>{unitLabel(g.unit) || g.unit}</td>
                    <td className="n">{ranged(g.model_value, g.model_lo, g.model_hi, g.unit, false)}</td>
                    <td className="n">{ranged(g.market_value, g.market_lo, g.market_hi, g.unit, false)}</td>
                    <td className="n">{pct(g.distance, 1)}</td>
                    <td>{g.overlap ? "yes" : "no"}</td>
                    <td className="n">{g.days_apart}</td>
                    <td>{g.flagged ? <b>flagged</b> : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="dim">
            Distance is (market mid − model mid) / model mid. Market prices from Hyperliquid, one venue; not the whole
            market.
          </p>

          {gaps.map((g) => (
            <section key={g.node} id={g.node}>
              <h2>
                {g.label}
                {g.flagged ? " (flagged)" : ""}
              </h2>
              <GapChart points={points.filter((p) => p.node === g.node)} label={g.label} unit={g.unit} />
              <GapDetailView gap={g} />
            </section>
          ))}
        </>
      )}
    </>
  );
}
