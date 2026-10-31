import { definitions } from "../../lib/queries";
import { HANDLE } from "../../lib/urls";

export const revalidate = 300;
export const metadata = { title: "Method" };

type Defs = {
  cohorts?: Record<string, number>;
  coverage?: Record<string, number>;
  events?: Record<string, number>;
  daily?: Record<string, number>;
  legs?: Record<string, { label: string; markets: string[] }>;
  leg_order?: string[];
};

const usd = (x?: number) => (x == null ? "?" : `$${x.toLocaleString("en-US")}`);
const p = (x?: number, d = 0) => (x == null ? "?" : `${(x * 100).toFixed(d)}%`);

export default async function Method() {
  const d = (await definitions()) as Defs;
  const c = d.cohorts ?? {};
  const e = d.events ?? {};
  const cov = d.coverage ?? {};
  return (
    <>
      <h1>Method</h1>
      <p>
        Every number on this site is computed from public Hyperliquid chain state and can be reproduced without asking
        anyone. The values below are the ones the last hourly run used; they come from the same configuration the
        indexer reads.
      </p>

      <h2>Where positions come from</h2>
      <p>
        The indexer subscribes to the public <code>trades</code> WebSocket channel for every market in the universe.
        Every fill names both counterparties, which is how addresses are discovered. Each discovered address is then
        snapshotted once an hour with <code>clearinghouseState</code> for each dex it trades on, which returns its exact
        open positions. Positions are never reconstructed from fills; fills only move a running position between
        snapshots so alerts can fire in real time, and the next snapshot replaces it. Addresses with no position and no
        fill for 14 days leave the snapshot set and return the moment they trade. Large accounts from the public
        Hyperliquid leaderboard are snapshotted once at start so that positions opened before the tape started are seen.
      </p>

      <h2>Universe</h2>
      <p>
        The AI complex is every Hyperliquid perp whose price is a bet on AI capex, grouped into legs. The list is checked
        against live market metadata on every start; delisted markets drop out. Native crypto perps are excluded.
      </p>
      <ul>
        {(d.leg_order ?? Object.keys(d.legs ?? {})).map((k) => {
          const l = d.legs?.[k];
          return l ? (
            <li key={k}>
              {l.label}: {l.markets.join(", ")}
            </li>
          ) : null;
        })}
      </ul>

      <h2>Cohorts</h2>
      <ul>
        <li>
          Whales: the top {c.whale_count ?? "?"} addresses by gross notional across the complex, bots excluded,
          recomputed once a day from the {c.recompute_hour_et ?? "?"}:00 ET snapshot and fixed for that day.
        </li>
        <li>Mid: every other address with at least {usd(c.mid_floor_usd)} gross notional across the complex.</li>
        <li>Small: everyone under {usd(c.mid_floor_usd)}.</li>
        <li>
          Fresh (overlay): addresses whose first fill in the complex is under {c.fresh_days ?? "?"} days old. Hyperliquid
          serves only an address&apos;s most recent 10,000 fills; an address whose history is truncated cannot be dated
          and is not counted fresh, so the overlay understates rather than invents.
        </li>
        <li>
          Bots: over {c.bot_fills_per_day ?? "?"} fills a day (over the last {c.bot_lookback_days ?? "?"} days) and a
          median hold under {c.bot_median_hold_s ? c.bot_median_hold_s / 60 : "?"} minutes, matching closes to opens
          first-in first-out. Shown separately; market makers are not positioning.
        </li>
      </ul>

      <h2>Series</h2>
      <p>Computed hourly per market and rolled up per leg and for the whole complex. Notional is size times mark.</p>
      <table>
        <tbody>
          <tr><td>whale_net</td><td>(whale longs − whale shorts) / open interest, signed</td></tr>
          <tr><td>whale_net_change</td><td>whale_net now minus 24 hours earlier</td></tr>
          <tr><td>mid_net, small_net, bot_net</td><td>the same for the other cohorts; all four sum to longs − shorts known</td></tr>
          <tr><td>fresh_share</td><td>gross notional held by fresh addresses / open interest</td></tr>
          <tr><td>crowding</td><td>gross notional of the top {c.crowding_top_n ?? 5} addresses / open interest</td></tr>
          <tr><td>coverage</td><td>known long notional / open interest</td></tr>
          <tr><td>imbalance</td><td>(known longs − known shorts) / open interest; zero when coverage is complete</td></tr>
          <tr><td>funding, funding_ann</td><td>hourly funding as the API reports it; ×24×365</td></tr>
          <tr><td>premium_bps</td><td>(mark − oracle) / oracle, basis points</td></tr>
          <tr><td>liq_share</td><td>confirmed liquidated notional in the hour before the snapshot / open interest</td></tr>
          <tr><td>oi_usd, mark</td><td>open interest in USD; mark price</td></tr>
        </tbody>
      </table>
      <p>
        Open interest is one side of the book. Gross notional counts both sides of an address, so fresh share and
        crowding can exceed 100% in principle. Leg and complex values are open-interest-weighted: numerators and
        denominators are summed in USD across the markets, so long NVDA and short CRWV show up at complex level as what
        they are, a spread.
      </p>

      <h2>Reconciliation and coverage</h2>
      <p>
        Longs equal shorts on every perp, so aggregate positioning means nothing until it is cut by cohort, and every cut
        has to sum back. Each hour the sum of every long position the tape knows on a market is compared with the
        market&apos;s open interest. That ratio is coverage, published with every row of every series. A gap means
        addresses are missing from the snapshot set. Charts draw a line dashed where coverage is under{" "}
        {p(cov.dashed_below)}, and the daily post is not made when complex-level coverage is under {p(cov.post_min)}: a gap
        day beats a wrong chart. Daily rows in the data are written only for subjects over the bar.
      </p>

      <h2>Events</h2>
      <ul>
        <li>Whale flip: a whale goes from net long to net short (or back) on a market with over {usd(e.whale_flip_min_usd)} on the new side.</li>
        <li>Whale entry: an address outside the whale set opens over {usd(e.whale_entry_min_usd)} on one market.</li>
        <li>Whale net move: whale_net_change over {p(e.whale_net_move)} of open interest on a market.</li>
        <li>Funding over {p(e.funding_hourly, 2)} an hour in either direction, or premium over {e.premium_bps ?? "?"} bp.</li>
        <li>Liquidations: over {p(e.liquidation_share)} of a market&apos;s open interest liquidated in one hour.</li>
      </ul>
      <p>
        Liquidations are candidates when an address&apos;s position shrinks on a fill within 1% of its last snapshot
        liquidation price, and are counted only once that address&apos;s own fill history marks the fill as a
        liquidation.
      </p>

      <h2>What the tape cannot see</h2>
      <p>
        Anything not on Hyperliquid: listed equities and options, private credit, OTC and swap books, other venues&apos;
        perps. Positions held through vaults or sub-accounts show under that vault or sub-account. Addresses are
        addresses: one person can hold many, and one address can be a fund. The tape never names a person.
      </p>

      <h2>Licence</h2>
      <p>Charts and data are CC BY 4.0. Attribution: {HANDLE}.</p>
    </>
  );
}
