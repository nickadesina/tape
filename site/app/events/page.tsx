import Link from "next/link";
import { shortAddr, usd, utc } from "../../lib/format";
import { events } from "../../lib/queries";
import { EXPLORER, chartPage, slug } from "../../lib/urls";

export const revalidate = 300;
export const metadata = { title: "Events" };

const KIND: Record<string, string> = {
  whale_flip: "whale flip",
  whale_entry: "whale entry",
  whale_net_move: "whale net move",
  funding: "funding",
  premium: "premium",
  liquidations: "liquidations",
  regime_extreme: "one-asset regime",
  tension_extreme: "graph against market",
  deal_study: "deal study",
};

function what(e: { kind: string; detail: Record<string, number | string | null> }): string {
  const d = e.detail ?? {};
  const n = (k: string) => (typeof d[k] === "number" ? (d[k] as number) : null);
  switch (e.kind) {
    case "whale_flip":
      return `flipped ${d.new_side}`;
    case "whale_entry":
      return `opened ${d.side}`;
    case "whale_net_move":
      return `${((n("change") ?? 0) * 100).toFixed(1)}% of OI in 24h`;
    case "funding":
      return `${((n("funding") ?? 0) * 100).toFixed(4)}%/h`;
    case "premium":
      return `${(n("premium_bps") ?? 0).toFixed(0)} bp to oracle`;
    case "liquidations":
      return `${((n("liq_share") ?? 0) * 100).toFixed(1)}% of OI in 1h`;
    case "regime_extreme":
      return `labs and ${d.basket}: co-movement ${(n("corr") ?? 0).toFixed(2)}${d.since ? `, highest since ${d.since}` : ", highest on record"}`;
    case "tension_extreme":
      return `${d.dst_name} moves ${((n("beta") ?? 0) * 100).toFixed(0)}% with ${d.src_name} on a ${((n("share") ?? 0) * 100).toFixed(0)}% revenue share`;
    case "deal_study": {
      const f = (k: string) => (n(k) == null ? "n/a" : `${((n(k) as number) * 100).toFixed(1)}%`);
      return `${d.src_name} → ${d.dst_name}: receiver ${f("dst_day0")} day 0, ${f("dst_day_n")} day ${d.days}`;
    }
    default:
      return "";
  }
}

export default async function Events() {
  const rows = await events(500);
  return (
    <>
      <h1>Events</h1>
      <p>
        Every alert that fired, newest first. Thresholds are on the <Link href="/method">method</Link> page.
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>time</th>
              <th>market</th>
              <th>event</th>
              <th />
              <th className="n">size</th>
              <th>address</th>
              <th>chart</th>
              <th>post</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td>{utc(e.time)}</td>
                <td>
                  {e.coin.includes(":") && !/^(regime|edge):/.test(e.coin) ? (
                    <Link href={`/m/${slug(e.coin)}`}>{e.coin}</Link>
                  ) : (
                    <Link href="/map">{e.coin.replace(/^(regime|edge):/, "")}</Link>
                  )}
                </td>
                <td>{KIND[e.kind] ?? e.kind}</td>
                <td>{what(e)}</td>
                <td className="n">{e.size_usd ? usd(e.size_usd) : ""}</td>
                <td>{e.address ? <a href={`${EXPLORER}${e.address}`}>{shortAddr(e.address)}</a> : ""}</td>
                <td>{e.event_chart_hour ? <Link href={chartPage("event", e.id, e.event_chart_hour)}>chart</Link> : ""}</td>
                <td>{e.post_url ? <a href={e.post_url}>post</a> : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="dim">No events yet.</p>}
    </>
  );
}
