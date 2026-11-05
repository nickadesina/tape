import Link from "next/link";
import { scenarioList } from "../../lib/queries";

export const revalidate = 300;
export const metadata = {
  title: "Scenarios",
  description: "One input to the stack model changed and pushed through it: what moves, and whose revenue is at risk.",
};

export default async function ScenariosPage() {
  const list = await scenarioList();
  return (
    <>
      <h1>Scenarios</h1>
      <p>
        Each scenario changes one input to the <Link href="/stack">stack model</Link> and pushes it through the
        quantities that depend on it, step by step, to the revenue it puts at risk at each company. Every step names the
        relationship it travels through. Ranges stay ranges the whole way.
      </p>
      {list.length === 0 ? (
        <p className="dim">No scenario run yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>scenario</th>
              <th>latest</th>
              <th className="n">days run</th>
            </tr>
          </thead>
          <tbody>
            {list.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link href={`/scenarios/${s.id}`}>{s.label}</Link> <span className="dim">{s.id}</span>
                </td>
                <td>{s.day}</td>
                <td className="n">{s.days}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
