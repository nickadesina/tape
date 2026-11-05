import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";
import { pct, pp, usd } from "../lib/format";
import { slug } from "../lib/urls";

export type Metrics = Record<string, { value: number | null; coverage: number | null }>;

export function Cls({ x }: { x: number | null | undefined }) {
  return <span className={x == null ? "" : x < 0 ? "neg" : x > 0 ? "pos" : ""}>{pp(x)}</span>;
}

/** Whale net with a diverging bar: zero in the middle, ±100% of OI at the ends. */
export function Net({ x }: { x: number | null | undefined }) {
  if (x == null || Number.isNaN(x)) return <>{pct(x)}</>;
  const style = { "--m": Math.min(Math.abs(x), 1) } as CSSProperties;
  return (
    <>
      <span className={`dv ${x < 0 ? "neg" : "pos"}`} style={style} aria-hidden="true">
        <i />
      </span>
      {pct(x)}
    </>
  );
}

/** Section head with its Japanese gloss (decorative; screen readers get the English). */
export function H2({ id, ja, children }: { id?: string; ja?: string; children: ReactNode }) {
  return (
    <h2 id={id}>
      <span>{children}</span>
      {ja && (
        <span className="ja" lang="ja" aria-hidden="true">
          {ja}
        </span>
      )}
    </h2>
  );
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
            <th className="n">coverage</th>
            <th className="n">fresh</th>
            <th className="n">funding/h</th>
            <th className="n">OI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.coin}>
              <td>
                <Link href={`/m/${slug(m.coin)}`}>{m.coin}</Link>
              </td>
              <td className="n">
                <Net x={m.s.whale_net?.value} />
              </td>
              <td className="n">
                <Cls x={m.s.whale_net_change?.value} />
              </td>
              <td className="n">{pct(m.s.coverage?.value, 1, false)}</td>
              <td className="n">{pct(m.s.fresh_share?.value, 1, false)}</td>
              <td className="n">{pct(m.s.funding?.value, 4)}</td>
              <td className="n">{usd(m.s.oi_usd?.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
