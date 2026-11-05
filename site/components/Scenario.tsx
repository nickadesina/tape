import Link from "next/link";
import { amount, pct, unitLabel, usd } from "../lib/format";
import type { Scenario, Triple } from "../lib/queries";

function triple(t: Triple, unit: string): string {
  const u = unitLabel(unit);
  return `[${t.map((x) => amount(x, unit)).join(", ")}]${u ? ` ${u}` : ""}`;
}

/** One scenario run: the shock, the path it travels, and the revenue at risk. */
export function ScenarioView({ scenario: s, days }: { scenario: Scenario; days: string[] }) {
  const change = s.shock.change;
  return (
    <>
      <h1>{s.label}</h1>
      <p className="dim">
        Scenario {s.id}, run {s.day}. <Link href="/scenarios">All scenarios</Link>.
      </p>

      <h2>The shock</h2>
      <p>
        <Link href={`/stack#${s.shock.key}`}>{s.shock.key}</Link>{" "}
        {typeof change === "number" ? pct(change, 1) : change}
        {s.shock.note ? `: ${s.shock.note}` : ""}
      </p>

      <h2>How it travels</h2>
      {s.path.length === 0 ? (
        <p className="dim">Nothing downstream of this input moved.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th className="n">step</th>
                <th>quantity</th>
                <th>unit</th>
                <th className="n">base [lo, mid, hi]</th>
                <th className="n">shocked [lo, mid, hi]</th>
                <th>via</th>
              </tr>
            </thead>
            <tbody>
              {s.path.map((p, i) => (
                <tr key={`${p.key}-${i}`}>
                  <td className="n">{i + 1}</td>
                  <td>
                    {p.label} <Link href={`/stack#${p.key}`} className="dim">{p.key}</Link>
                  </td>
                  <td>{p.unit}</td>
                  <td className="n">{triple(p.base, p.unit)}</td>
                  <td className="n">→ {triple(p.shocked, p.unit)}</td>
                  <td>{p.via ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Revenue at risk</h2>
      {s.revenue_at_risk.length === 0 ? (
        <p className="dim">No company revenue on the path.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>company</th>
                <th className="n">lo</th>
                <th className="n">mid</th>
                <th className="n">hi</th>
                <th>via</th>
              </tr>
            </thead>
            <tbody>
              {s.revenue_at_risk.map((x) => (
                <tr key={x.entity}>
                  <td>
                    <Link href={`/node/${x.entity}`}>{x.name}</Link>
                  </td>
                  <td className="n">{usd(x.lo)}</td>
                  <td className="n">{usd(x.mid)}</td>
                  <td className="n">{usd(x.hi)}</td>
                  <td>{x.via ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {days.length > 1 && (
        <>
          <h2>Other runs</h2>
          <p>
            {days.map((d, i) => (
              <span key={d}>
                {i > 0 ? ", " : ""}
                {d === s.day ? <b>{d}</b> : <Link href={i === 0 ? `/scenarios/${s.id}` : `/scenarios/${s.id}/${d}`}>{d}</Link>}
              </span>
            ))}
          </p>
        </>
      )}
      <p className="dim">
        A scenario is arithmetic on the model&apos;s ranges, not a forecast. <Link href="/method#stack">Method</Link>.
      </p>
    </>
  );
}
