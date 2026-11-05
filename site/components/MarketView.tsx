import Link from "next/link";
import type { ReactNode } from "react";
import { notFound } from "next/navigation";
import { age, bps, pct, shortAddr, usd, utc } from "../lib/format";
import { chartHour, events, latestHour, market, seriesAt, topPositions } from "../lib/queries";
import { EXPLORER, chartPng, slug, unslug } from "../lib/urls";
import { Chart, Status } from "./Chart";
import { Cls, H2 } from "./Tables";

export const WINDOWS = ["7d", "30d", "90d"] as const;

export async function marketMeta(slugParam: string, window: string) {
  const coin = unslug(slugParam);
  const h = await chartHour("card", coin);
  const images = h ? [{ url: chartPng("card", coin, h), width: 1200, height: 675 }] : [];
  return {
    title: `${coin} positioning, ${window}`,
    openGraph: { title: `${coin}: who is positioned which way`, images },
    twitter: { images: images.map((i) => i.url) },
  };
}

export async function MarketView({ slugParam, window }: { slugParam: string; window: string }) {
  if (!(WINDOWS as readonly string[]).includes(window)) notFound();
  const coin = unslug(slugParam);
  const m = await market(coin);
  if (!m) notFound();
  const hour = await latestHour();
  const [s, cardH, panelH, tops, evs] = await Promise.all([
    hour ? seriesAt(hour, [coin]) : Promise.resolve({}),
    chartHour("card", coin),
    chartHour("market", `${coin}@${window}`),
    hour ? topPositions(coin, hour) : Promise.resolve([]),
    events(20, coin),
  ]);
  const x = (s as Record<string, Record<string, { value: number | null }>>)[coin] ?? {};
  const v = (k: string) => x[k]?.value ?? null;
  const now = hour ?? new Date();

  const oi = v("oi_usd");
  const spec: [string, ReactNode][] = [
    ["mid", pct(v("mid_net"))],
    ["small", pct(v("small_net"))],
    ["bots", pct(v("bot_net"))],
    ["fresh", pct(v("fresh_share"), 1, false)],
    ["crowding", pct(v("crowding"), 1, false)],
    [
      "funding / h",
      <>
        {pct(v("funding"), 4)} <small>{pct(v("funding_ann"), 0)} ann.</small>
      </>,
    ],
    ["premium", bps(v("premium_bps"))],
    ["liquidated, last hour", <>{pct(v("liq_share"), 2, false)} <small>of OI</small></>],
    ["open interest", usd(oi)],
    ["coverage", pct(v("coverage"), 1, false)],
  ];

  return (
    <>
      <Status hour={hour} />
      <h1>{coin}</h1>
      <p className="sub">
        {m.label} · max {m.max_leverage}x
      </p>
      <p className="lede">
        Whale net <b>{pct(v("whale_net"))}</b> of OI, 24h{" "}
        <b>
          <Cls x={v("whale_net_change")} />
        </b>
        .
      </p>
      <dl className="spec">
        {spec.map(([k, val]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{val}</dd>
          </div>
        ))}
      </dl>
      {cardH && <Chart kind="card" subject={coin} hour={cardH} alt={`${coin} net positioning by cohort, 7 days`} />}
      <div className="seg-row">
        <span id="window-label">All metrics, window</span>
        <nav className="seg" aria-labelledby="window-label">
          {WINDOWS.map((w) =>
            w === window ? (
              <span key={w} aria-current="page">
                {w}
              </span>
            ) : (
              <Link key={w} href={w === "30d" ? `/m/${slug(coin)}` : `/m/${slug(coin)}/${w}`}>
                {w}
              </Link>
            ),
          )}
        </nav>
      </div>
      {panelH && (
        <Chart
          kind="market"
          subject={`${coin}@${window}`}
          hour={panelH}
          alt={`${coin} metrics, ${window}`}
          width={1200}
          height={1000}
        />
      )}

      <H2 ja="上位">Top {tops.length} addresses</H2>
      {hour && <p className="note">Positions at {utc(hour)}.</p>}
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th className="n">#</th>
              <th>address</th>
              <th>side</th>
              <th className="n">notional</th>
              <th className="n">% OI</th>
              <th>cohort</th>
              <th className="n">held</th>
            </tr>
          </thead>
          <tbody>
            {tops.map((t) => (
              <tr key={t.rank}>
                <td className="n">{t.rank}</td>
                <td>
                  <a href={`${EXPLORER}${t.address}`} title={t.address}>
                    {shortAddr(t.address)}
                  </a>
                </td>
                <td className={t.size > 0 ? "pos" : "neg"}>{t.size > 0 ? "long" : "short"}</td>
                <td className="n">{usd(t.notional)}</td>
                <td className="n">{pct(oi ? t.notional / oi : null, 1, false)}</td>
                <td>
                  {t.cohort}
                  {t.fresh ? " · fresh" : ""}
                </td>
                <td className="n">{age(t.since_hour, now)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">
        Held: hours since the position last changed sign, from hourly snapshots; positions open before the tape started
        count from its first sweep.
      </p>

      {evs.length > 0 && (
        <>
          <H2 ja="速報">Recent events</H2>
          <div className="scroll">
            <table>
              <tbody>
                {evs.map((e) => (
                  <tr key={e.id}>
                    <td>{utc(e.time)}</td>
                    <td>{e.kind.replace(/_/g, " ")}</td>
                    <td className="n">{e.size_usd ? usd(e.size_usd) : ""}</td>
                    <td>{e.post_url && <a href={e.post_url}>post</a>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
