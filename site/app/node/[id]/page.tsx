import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { GapChart, GapDetailView, KindLabel, QuantityTable, SourceCell } from "../../../components/Stack";
import { pct, period, ranged, unitLabel, usd, utc } from "../../../lib/format";
import {
  type Quantity,
  SEGMENTS,
  currentQuantities,
  entity,
  entityLatestGaps,
  gapHistory,
  nodeEdges,
  quantityHistory,
} from "../../../lib/queries";
import { slug } from "../../../lib/urls";

export const revalidate = 300;

// No paths at build time; each is rendered on first request and then cached.
export async function generateStaticParams() {
  return [];
}

export async function generateMetadata(props: PageProps<"/node/[id]">): Promise<Metadata> {
  const { id } = await props.params;
  const e = await entity(id);
  if (!e) return {};
  return { title: e.name, description: `${e.name} in the stack model: sourced figures, revisions and gaps.` };
}

const TYPE: Record<string, string> = {
  equity: "equity",
  compute_contract: "compute contract",
  supply: "supply",
  debt_lease: "debt or lease",
  revenue_concentration: "revenue share",
  guarantee: "guarantee",
};

export default async function NodePage(props: PageProps<"/node/[id]">) {
  const { id } = await props.params;
  const e = await entity(id);
  if (!e) notFound();
  const [current, history, gaps, points, edges] = await Promise.all([
    currentQuantities(e.id),
    quantityHistory(e.id),
    entityLatestGaps(e.id),
    gapHistory(90, e.id),
    e.node ? nodeEdges(e.node) : Promise.resolve([]),
  ]);
  const versions: Record<string, Quantity[]> = {};
  for (const q of history) (versions[q.key] ??= []).push(q);
  const segment = SEGMENTS.find((s) => s.key === e.segment)?.label ?? e.segment;
  const ids = Object.entries(e.ids ?? {});
  return (
    <>
      <h1>{e.name}</h1>
      <p>
        {e.kind}, <Link href={`/stack#${e.segment}`}>{segment}</Link>
        {e.markets.length > 0 && (
          <>
            {". Priced on Hyperliquid by "}
            {e.markets.map((m, j) => (
              <span key={m}>
                {j > 0 ? ", " : ""}
                <Link href={`/m/${slug(m)}`}>{m}</Link>
              </span>
            ))}
          </>
        )}
        .
      </p>
      {ids.length > 0 && (
        <p className="dim">
          {ids.map(([k, v], i) => (
            <span key={k}>
              {i > 0 ? ", " : ""}
              {k} {String(v)}
            </span>
          ))}
        </p>
      )}
      {e.note && <p>{e.note}</p>}

      <h2>Current figures</h2>
      {current.length === 0 ? (
        <p className="dim">None verified yet.</p>
      ) : (
        <QuantityTable rows={current} />
      )}

      <h2>Every version</h2>
      {history.length === 0 ? (
        <p className="dim">None.</p>
      ) : (
        Object.entries(versions).map(([key, rows]) => (
          <section key={key}>
            <p>
              <b>{key}</b>
            </p>
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>period</th>
                    <th className="n">version</th>
                    <th className="n">value [range]</th>
                    <th>kind</th>
                    <th>source</th>
                    <th>from</th>
                    <th>until</th>
                    <th>reason</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((q) => (
                    <tr key={q.id} className={q.valid_to ? "dim" : undefined}>
                      <td>{period(q.period_start, q.period_end)}</td>
                      <td className="n">{q.version}</td>
                      <td className="n">{ranged(q.value, q.lo, q.hi, q.unit)}</td>
                      <td>
                        <KindLabel kind={q.kind} />
                      </td>
                      <td>
                        <SourceCell q={q} />
                      </td>
                      <td>{utc(q.valid_from).slice(0, 10)}</td>
                      <td>{q.valid_to ? utc(q.valid_to).slice(0, 10) : "current"}</td>
                      <td>{q.revision_reason ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}

      <h2>Gaps</h2>
      {gaps.length === 0 ? (
        <p className="dim">No market-implied comparison for this entity yet.</p>
      ) : (
        gaps.map((g) => {
          const pts = points.filter((p) => p.node === g.node);
          return (
            <section key={g.node} id={`gap-${g.node}`}>
              <p>
                <b>{g.label}</b>{" "}
                <span className="dim">
                  ({g.node}, {unitLabel(g.unit) || g.unit}, latest {g.day})
                </span>
              </p>
              <GapChart points={pts} label={g.label} unit={g.unit} />
              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>day</th>
                      <th className="n">model range</th>
                      <th className="n">market range</th>
                      <th className="n">distance</th>
                      <th className="n">days apart</th>
                      <th>flagged</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...pts].reverse().map((p) => (
                      <tr key={p.day} className={p.flagged ? "neg" : undefined}>
                        <td>{p.day}</td>
                        <td className="n">{ranged(p.model_value, p.model_lo, p.model_hi, p.unit, false)}</td>
                        <td className="n">{ranged(p.market_value, p.market_lo, p.market_hi, p.unit, false)}</td>
                        <td className="n">{pct(p.distance, 1)}</td>
                        <td className="n">{p.days_apart}</td>
                        <td>{p.flagged ? <b>flagged</b> : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <GapDetailView gap={g} />
            </section>
          );
        })
      )}

      <h2>Financing</h2>
      {!e.node ? (
        <p className="dim">Not in the financing graph.</p>
      ) : edges.length === 0 ? (
        <p className="dim">No verified edge into or out of this name yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>from</th>
                <th>to</th>
                <th>type</th>
                <th className="n">size</th>
                <th>status</th>
                <th>disclosed</th>
                <th>source</th>
                <th>checked</th>
              </tr>
            </thead>
            <tbody>
              {edges.map((x) => (
                <tr key={x.id} title={x.note ?? undefined}>
                  <td>{x.src_name}</td>
                  <td>{x.dst_name}</td>
                  <td>
                    {TYPE[x.type] ?? x.type}
                    {x.share != null ? ` ${pct(x.share, 0, false)}` : ""}
                  </td>
                  <td className="n">{x.size_usd ? usd(x.size_usd) : ""}</td>
                  <td>{x.status ?? ""}</td>
                  <td>{x.date}</td>
                  <td>
                    <a href={x.source}>{x.confidence}</a>
                  </td>
                  <td className={x.stale ? "neg" : undefined}>
                    {x.verified_at}
                    {x.stale ? " stale" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="dim">
        <Link href="/map">The financing map</Link>, <Link href="/stack">the stack model</Link>,{" "}
        <Link href="/gaps">gaps</Link>.
      </p>
    </>
  );
}
