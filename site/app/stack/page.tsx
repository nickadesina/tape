import Link from "next/link";
import { CheckedCell, KindLabel, SourceCell } from "../../components/Stack";
import { period, ranged, utc } from "../../lib/format";
import { type Quantity, SEGMENTS, currentQuantities, entities, quantityRevisions } from "../../lib/queries";

export const revalidate = 300;
export const metadata = {
  title: "Stack model",
  description:
    "A sourced, bottom-up model of the AI supply chain, from demand through chips, memory, power and cloud to financing.",
};

export default async function StackPage() {
  const [ents, current, revisions] = await Promise.all([entities(), currentQuantities(), quantityRevisions()]);
  const byEntity: Record<string, Quantity[]> = {};
  for (const q of current) (byEntity[q.entity] ??= []).push(q);
  const segments = [...SEGMENTS, ...[...new Set(ents.map((e) => e.segment))]
    .filter((s) => !SEGMENTS.some((x) => x.key === s))
    .map((s) => ({ key: s, label: s }))];
  return (
    <>
      <h1>The stack model</h1>
      <p>
        What the AI build-out can physically deliver, layer by layer: demand, accelerators, memory, packaging, power,
        cloud capacity, the economics of renting a GPU, and the financing behind it. Each figure is filed,
        company-stated, reported, or an <b>estimate</b> with its method and inputs stated, and carries a range. Figures
        not re-checked against their source in time are marked stale. The <Link href="/gaps">gaps</Link> page sets
        these against what the market prices; the <Link href="/method#stack">method</Link> says how.
      </p>

      {current.length === 0 ? (
        <p className="dim">
          No figures yet. The model&apos;s figures ship once a person has verified each one against its source, like
          the <Link href="/map">map</Link>&apos;s edges.
        </p>
      ) : (
        segments.map((s) => {
          const list = ents.filter((e) => e.segment === s.key);
          if (list.length === 0) return null;
          return (
            <section key={s.key}>
              <h2 id={s.key}>{s.label}</h2>
              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>entity</th>
                      <th>metric</th>
                      <th className="n">value [range]</th>
                      <th>period</th>
                      <th>kind</th>
                      <th>source</th>
                      <th>checked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((e) => {
                      const qs = byEntity[e.id] ?? [];
                      if (qs.length === 0)
                        return (
                          <tr key={e.id}>
                            <td>
                              <Link href={`/node/${e.id}`}>{e.name}</Link>
                            </td>
                            <td colSpan={6} className="dim">
                              no current figures
                            </td>
                          </tr>
                        );
                      return qs.map((q, i) => (
                        <tr key={q.id} id={q.key}>
                          <td>{i === 0 ? <Link href={`/node/${e.id}`}>{e.name}</Link> : ""}</td>
                          <td>{q.metric}</td>
                          <td className="n">{ranged(q.value, q.lo, q.hi, q.unit)}</td>
                          <td>{period(q.period_start, q.period_end)}</td>
                          <td>
                            <KindLabel kind={q.kind} />
                          </td>
                          <td>
                            <SourceCell q={q} />
                          </td>
                          <td>
                            <CheckedCell q={q} />
                          </td>
                        </tr>
                      ));
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })
      )}

      <h2 id="revisions">Revisions</h2>
      <p>
        Every figure that changed after it shipped, against the version it replaced, newest first. Nothing is
        overwritten: each version stays in the database and on its entity&apos;s page.
      </p>
      {revisions.length === 0 ? (
        <p className="dim">None yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>key</th>
                <th>period</th>
                <th className="n">old</th>
                <th className="n">new</th>
                <th>reason</th>
                <th>date</th>
              </tr>
            </thead>
            <tbody>
              {revisions.map((r) => (
                <tr key={`${r.key}-${r.period_start}-${r.period_end}-${r.version}`}>
                  <td>
                    <Link href={`/node/${r.entity}#${r.key}`}>{r.key}</Link> <span className="dim">v{r.version}</span>
                  </td>
                  <td>{period(r.period_start, r.period_end)}</td>
                  <td className="n">
                    {r.old_value == null ? "n/a" : ranged(r.old_value, r.old_lo ?? r.old_value, r.old_hi ?? r.old_value, r.unit)}
                  </td>
                  <td className="n">→ {ranged(r.value, r.lo, r.hi, r.unit)}</td>
                  <td>{r.revision_reason ?? ""}</td>
                  <td>{utc(r.valid_from).slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
