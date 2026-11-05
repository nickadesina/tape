import Link from "next/link";
import { pct } from "../../lib/format";
import { stressRuns } from "../../lib/queries";

export const revalidate = 300;
export const metadata = {
  title: "Stress tests",
  description:
    "Fragility of the market's view: a monthly stress test on real Hyperliquid positions in the AI complex.",
};

export default async function StressIndex() {
  const runs = await stressRuns();
  return (
    <>
      <h1>Stress tests</h1>
      <p>
        Fragility of the market&apos;s view, an exhibit: how much forced selling the positions behind today&apos;s prices
        would set off if one name fell. Once a month, on a Sunday before Asia opens, each lab, neocloud and memory name is shocked by 10%, 20% and 30% on the
        positions Hyperliquid held at the 15:00 ET snapshot. The test finds the accounts that breach their margin and
        walks their forced selling through the real order books, round after round. A run is published only when
        coverage is at the bar, the model reproduces recorded liquidations, and its wording has been reviewed. The{" "}
        <Link href="/method#stress">method</Link> says how.
      </p>
      {runs.length === 0 ? (
        <p className="dim">No published run yet.</p>
      ) : (
        <ul>
          {runs.map((r) => (
            <li key={r.date}>
              <Link href={`/stress/${r.date}`}>{r.date}</Link>{" "}
              <span className="dim">coverage {pct(r.coverage, 1, false)}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
