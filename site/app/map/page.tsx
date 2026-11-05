import Link from "next/link";
import { pct, usd } from "../../lib/format";
import {
  type GraphEdge,
  type GraphNode,
  type StandingRow,
  dealQuarters,
  dealStudies,
  graphCorrections,
  graphEdges,
  graphNodes,
  standingLatest,
} from "../../lib/queries";
import { slug } from "../../lib/urls";

export const revalidate = 300;
export const metadata = {
  title: "Map",
  description: "Who invests in, buys from and lends to whom across the AI build-out, with a source on every edge.",
};

const KIND: Record<string, string> = {
  lab: "Labs",
  neocloud: "Neoclouds",
  chip: "Chips",
  optics: "Optics",
  memory: "Memory",
  hyperscaler: "Hyperscalers",
  lender: "Lenders",
  fund: "Funds and ETFs",
  other: "Other",
};

const TYPE: Record<string, string> = {
  equity: "equity",
  compute_contract: "compute contract",
  supply: "supply",
  debt_lease: "debt or lease",
  revenue_concentration: "revenue share",
  guarantee: "guarantee",
};

function num(x: number | null | undefined, d = 2): string {
  return x == null ? "n/a" : x.toFixed(d);
}

function bySubject(rows: StandingRow[]): Record<string, Record<string, number | null>> {
  const out: Record<string, Record<string, number | null>> = {};
  for (const r of rows) (out[r.subject] ??= {})[r.metric] = r.value;
  return out;
}

export default async function MapPage() {
  const [nodes, edges, corrections, standing, deals, quarters] = await Promise.all([
    graphNodes(),
    graphEdges(),
    graphCorrections(),
    standingLatest(),
    dealStudies(),
    dealQuarters(),
  ]);
  const name = Object.fromEntries(nodes.map((n) => [n.key, n.name])) as Record<string, string>;
  const kinds = [...new Set(nodes.map((n) => n.kind))];
  const series = bySubject(standing);
  const regimes = Object.keys(series).filter((s) => s.startsWith("regime:"));
  const tensions = Object.keys(series).filter((s) => s.startsWith("edge:"));
  const edgeById = Object.fromEntries(edges.map((e) => [e.id, e])) as Record<string, GraphEdge>;
  const asOf = standing[0]?.hour;
  return (
    <>
      <h1>The financing map</h1>
      <p>
        Who invests in, buys from and lends to whom across the AI build-out, and the Hyperliquid markets that price each
        name. Direction follows the money. Every edge links its source and was checked by a person; edges not re-checked
        in 90 days are marked stale. The <Link href="/stress">stress test</Link> and the series below read this map.
      </p>

      <h2>Edges</h2>
      {edges.length === 0 ? (
        <p className="dim">No verified edges yet. Drafts are being checked against their filings before they ship.</p>
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
              {edges.map((e) => (
                <tr key={e.id} id={e.id} title={e.note ?? undefined}>
                  <td>{name[e.src] ?? e.src}</td>
                  <td>{name[e.dst] ?? e.dst}</td>
                  <td>
                    {TYPE[e.type] ?? e.type}
                    {e.share != null ? ` ${pct(e.share, 0, false)}` : ""}
                  </td>
                  <td className="n">{e.size_usd ? usd(e.size_usd) : ""}</td>
                  <td>{e.status ?? ""}</td>
                  <td>{e.date}</td>
                  <td>
                    <a href={e.source}>{e.confidence}</a>
                  </td>
                  <td className={e.stale ? "neg" : undefined}>
                    {e.verified_at}
                    {e.stale ? " stale" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Names</h2>
      {kinds.map((k) => (
        <p key={k}>
          <b>{KIND[k] ?? k}:</b>{" "}
          {nodes
            .filter((n: GraphNode) => n.kind === k)
            .map((n, i) => (
              <span key={n.key}>
                {i > 0 ? ", " : ""}
                {n.name}
                {n.markets.length > 0 && (
                  <>
                    {" ("}
                    {n.markets.map((m, j) => (
                      <span key={m}>
                        {j > 0 ? " " : ""}
                        <Link href={`/m/${slug(m)}`}>{m}</Link>
                      </span>
                    ))}
                    {")"}
                  </>
                )}
              </span>
            ))}
        </p>
      ))}

      <h2>Do labs and their suppliers trade as one?</h2>
      <p>
        Co-movement of daily returns between the labs basket and each supplier basket on Hyperliquid: over 30 days, and
        over sell-off days only (days the labs basket fell, 90 days). The percentile places today against the series&apos;
        own history. {asOf ? `As of the ${new Date(asOf.getTime() - 86_400_000).toISOString().slice(0, 10)} UTC close.` : ""}
      </p>
      {regimes.length === 0 ? (
        <p className="dim">Not computed yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>pair</th>
              <th className="n">30-day</th>
              <th className="n">sell-off days</th>
              <th className="n">percentile</th>
              <th className="n">history</th>
            </tr>
          </thead>
          <tbody>
            {regimes.map((s) => (
              <tr key={s}>
                <td>{s.replace("regime:", "").replace("~", " and ")}</td>
                <td className="n">{num(series[s].corr)}</td>
                <td className="n">{num(series[s].corr_selloff)}</td>
                <td className="n">{pct(series[s].corr_pctile, 0, false)}</td>
                <td className="n">{series[s].history_days ?? 0} days</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Graph against market</h2>
      <p>
        For each edge with a disclosed revenue share between two names that trade here: how much the dependent name moves
        when its counterparty does (beta, 90 days) against the share of revenue the filings say flows through that edge.
      </p>
      {tensions.length === 0 ? (
        <p className="dim">No verified revenue-share edge between two tradable names yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>edge</th>
              <th className="n">revenue share</th>
              <th className="n">beta</th>
              <th className="n">gap</th>
              <th className="n">gap percentile</th>
            </tr>
          </thead>
          <tbody>
            {tensions.map((s) => {
              const e = edgeById[s.replace("edge:", "")];
              return (
                <tr key={s}>
                  <td>{e ? `${name[e.dst]} from ${name[e.src]}` : s}</td>
                  <td className="n">{pct(series[s].share, 0, false)}</td>
                  <td className="n">{pct(series[s].beta, 0, false)}</td>
                  <td className="n">{pct(series[s].gap, 0)}</td>
                  <td className="n">{pct(series[s].gap_pctile, 0, false)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <h2>Deal studies</h2>
      <p>
        Every dated edge is an event: each name&apos;s move on Hyperliquid on the announcement day and five days later,
        from the close the day before. A deal is circular when money leaving the receiver comes back to the payer within
        three edges.
      </p>
      {quarters.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>quarter</th>
              <th className="n">circular deals</th>
              <th className="n">receivers, day 0</th>
              <th className="n">receivers, day 5</th>
            </tr>
          </thead>
          <tbody>
            {quarters.map((q) => (
              <tr key={q.quarter}>
                <td>{q.quarter}</td>
                <td className="n">{q.deals}</td>
                <td className="n">{pct(q.day0)}</td>
                <td className="n">{pct(q.day_n)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {deals.length === 0 ? (
        <p className="dim">No deal with Hyperliquid prices on both sides of its date yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>date</th>
                <th>deal</th>
                <th>market</th>
                <th className="n">day 0</th>
                <th className="n">day 5</th>
                <th>circular</th>
              </tr>
            </thead>
            <tbody>
              {deals.map((d) => (
                <tr key={`${d.edge}-${d.coin}`}>
                  <td>{d.date}</td>
                  <td>
                    <a href={`#${d.edge}`}>
                      {name[d.src]} → {name[d.dst]}
                    </a>
                  </td>
                  <td>
                    {d.coin} ({d.side === "src" ? "payer" : "receiver"})
                  </td>
                  <td className="n">{pct(d.day0)}</td>
                  <td className="n">{pct(d.day_n)}</td>
                  <td>{d.circular ? "yes" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Corrections</h2>
      {corrections.length === 0 ? (
        <p className="dim">None.</p>
      ) : (
        <ul>
          {corrections.map((c) => (
            <li key={`${c.date}-${c.edge}-${c.change.slice(0, 20)}`}>
              {c.date}
              {c.edge ? ` (${c.edge})` : ""}: {c.change}
            </li>
          ))}
        </ul>
      )}
      <p className="dim">
        Hyperliquid is one venue. Co-movement and deal reactions here describe that market, not the companies&apos;
        listed shares. Not advice.
      </p>
    </>
  );
}
