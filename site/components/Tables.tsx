import Link from "next/link";
import { pct, pp, usd } from "../lib/format";
import { slug } from "../lib/urls";

export type Metrics = Record<string, { value: number | null; coverage: number | null }>;

export function Cls({ x }: { x: number | null | undefined }) {
  return <span className={x == null ? "" : x < 0 ? "neg" : x > 0 ? "pos" : ""}>{pp(x)}</span>;
}

export function MarketTable({
  rows,
}: {
  rows: { coin: string; leg: string; s: Record<string, { value: number | null; coverage: number | null }> }[];
}) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>market</th>
            <th className="n">whale net</th>
            <th className="n">24h</th>
            <th className="n">fresh</th>
            <th className="n">funding/h</th>
            <th className="n">OI</th>
            <th className="n">coverage</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.coin}>
              <td>
                <Link href={`/m/${slug(m.coin)}`}>{m.coin}</Link>
              </td>
              <td className="n">{pct(m.s.whale_net?.value)}</td>
              <td className="n">
                <Cls x={m.s.whale_net_change?.value} />
              </td>
              <td className="n">{pct(m.s.fresh_share?.value, 1, false)}</td>
              <td className="n">{pct(m.s.funding?.value, 4)}</td>
              <td className="n">{usd(m.s.oi_usd?.value)}</td>
              <td className="n">{pct(m.s.coverage?.value, 1, false)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
