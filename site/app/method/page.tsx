import Link from "next/link";
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
  stress?: {
    shocks?: number[];
    node_kinds?: string[];
    max_rounds?: number;
    depth_window_days?: number;
    beta_days?: number;
    validation?: Record<string, number>;
  };
  loop?: Record<string, number | string>;
  graph?: { edges?: number; drafts?: number; stale_days?: number };
  stack?: { gap_persist_days?: number; stale_days?: number };
};

const usd = (x?: number) => (x == null ? "?" : `$${x.toLocaleString("en-US")}`);
const p = (x?: number, d = 0) => (x == null ? "?" : `${(x * 100).toFixed(d)}%`);

export default async function Method() {
  const d = (await definitions()) as Defs;
  const c = d.cohorts ?? {};
  const e = d.events ?? {};
  const cov = d.coverage ?? {};
  const st = d.stress ?? {};
  const v = st.validation ?? {};
  const lp = d.loop ?? {};
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

      <h2 id="margin">Account margin and order-book depth</h2>
      <p>
        The hourly <code>clearinghouseState</code> snapshot also returns each account&apos;s margin: account value, margin
        used, maintenance margin, and whether each position is cross or isolated. Margin on a HIP-3 dex is per dex, so
        an account on xyz and the same address on io are two accounts. The tape stores it for every account with a
        position in the complex, in the same transaction as the positions. Order-book depth comes from the public{" "}
        <code>l2Book</code> WebSocket: banded depth (USD within 2%, 5% and 10% of mid, each side) every minute, and a
        full book every hour. Hyperliquid sends at most 20 levels a side, which at full precision reaches a fraction of
        a percent on most of these books, so the tape also subscribes to the same book aggregated to 3 and 2 significant
        figures and stitches the views: the finest one out to where it stops, then each coarser one beyond. A coarse
        level can overlap the last fine one by up to one price bucket.
      </p>

      <h2 id="stress">Stress test: fragility of the market&apos;s view</h2>
      <p>
        The stress test is an exhibit, the fragility of the market&apos;s view: how much forced selling the positions
        behind the prices would set off if one name fell. Once a month, on a Sunday, it takes the 15:00 ET snapshot
        (every position, its leverage and liquidation price, each account&apos;s margin) and that hour&apos;s books, and
        shocks each{" "}
        {(st.node_kinds ?? ["lab", "neocloud", "memory"]).join(", ")} name by{" "}
        {(st.shocks ?? [-0.1, -0.2, -0.3]).map((x) => p(x)).join(", ")}. A name with markets on two venues (Anthropic on
        xyz and io) moves on both. A graph shock also moves every name whose revenue runs through the shocked lab, by
        its measured co-movement with that lab on the days it fell (last {st.beta_days ?? 90} days).
      </p>
      <ol>
        <li>Move the shocked markets by the shock.</li>
        <li>
          Recompute every account&apos;s equity and maintenance margin at the new prices: per account for cross margin
          (cross account value plus the PnL of its complex positions; positions outside the complex are held at their
          snapshot price), per position for isolated (its liquidation price). Maintenance margin is half the initial
          margin at the market&apos;s maximum leverage.
        </li>
        <li>Mark accounts below maintenance as liquidated and turn their positions into forced market orders.</li>
        <li>
          Walk each market&apos;s book with the forced flow to get the new price. The book re-forms around the shocked
          price and does not refill between rounds.
        </li>
        <li>Repeat until no new account breaches, or {st.max_rounds ?? 10} rounds.</li>
        <li>
          Report forced notional by market and round, the price path, the number of accounts hit (never which), and the
          amplification: the shocked name&apos;s final drawdown over the initial shock.
        </li>
      </ol>
      <p>
        Depth varies by hour, so every scenario runs three times: on the stored book scaled to the thick, median and thin
        5% depth for that hour over the last {st.depth_window_days ?? 28} days. The published figure is the median run;
        the range is the other two. Hyperliquid liquidates large positions partially and its backstop vault can absorb
        what the book cannot; the model closes positions in full at market and reports any flow past the book&apos;s reach
        separately, so forced selling is an upper bound.
      </p>
      <p>
        <b>Validation.</b> The model is only worth publishing once it reproduces what happened. Every drawdown of{" "}
        {p(v.min_move)} or more within {v.window_h ?? 24} hours of a snapshot is replayed: take that snapshot, apply the
        realized move, and compare the forced selling the model predicts with the liquidations the tape confirmed for
        the same positions. A run is published only when at least {v.min_episodes ?? 3} recent episodes with{" "}
        {usd(v.min_recorded_usd)} or more of recorded liquidations exist and {p(v.pass_share)} of them land within{" "}
        {p(v.tolerance)}; when complex coverage is at the bar; and after a person has reviewed the wording.
      </p>

      <h2 id="graph">The financing map</h2>
      <p>
        A hand-checked file of who invests in, buys from, supplies and lends to whom, with a linked filing, company
        announcement or report on every edge, its disclosure date and a confidence (filed, company-stated or reported).
        &ldquo;Announced&rdquo; (letters of intent, &ldquo;up to&rdquo;) is kept apart from &ldquo;contracted&rdquo;. An
        agent drafts edges; a person verifies each one before it ships, and edges not re-checked in{" "}
        {d.graph?.stale_days ?? 90} days are marked stale. Corrections are logged on the <Link href="/map">map</Link> page.
        Currently {d.graph?.edges ?? 0} verified edges.
      </p>

      <h2 id="series">Standing series</h2>
      <ul>
        <li>
          One-asset regime: co-movement (correlation of daily returns) between the labs basket and the neocloud, chip
          and memory baskets, over {lp.corr_days ?? 30} days and over sell-off days only (days the labs basket fell,{" "}
          {lp.selloff_days ?? 90} days). It alerts when co-movement reaches the {p(Number(lp.extreme_pctile ?? 0.95))}{" "}
          percentile of its own history, once there are {lp.min_history_days ?? 60} days of it.
        </li>
        <li>
          Graph against market: for each disclosed revenue share between two tradable names, the dependent name&apos;s
          beta to its counterparty over {lp.tension_days ?? 90} days against the share. The gap either way is the
          finding: an exposure the market ignores, or one it overprices.
        </li>
        <li>
          Deal studies: every dated edge is an event. Both names&apos; moves on the announcement day and{" "}
          {lp.deal_days ?? 5} days later, from the close the day before, and whether the deal is circular (money leaving
          the receiver comes back to the payer within three edges).
        </li>
      </ul>
      <p>
        Returns come from daily UTC closes on Hyperliquid. These series describe the market now; none is a prediction.
        A predictive claim (&ldquo;when X, Y follows&rdquo;) is published only after it passes a backtest and is logged
        with its horizon.
      </p>

      <h2 id="stack">Stack model and gaps</h2>
      <p>
        The <Link href="/stack">stack model</Link> says what is physically possible: a bottom-up account of the AI
        supply chain, from demand through accelerators, memory, packaging, power and cloud capacity to the economics of
        a rented GPU and the financing behind it. The market says what is priced. The engine measures the distance
        between the two, every day, on the <Link href="/gaps">gaps</Link> page.
      </p>
      <ul>
        <li>
          Every figure is filed, company-stated, reported, or an estimate. Filed and stated figures link their source
          and were checked by a person; figures not re-checked in {d.stack?.stale_days ?? "a set number of"} days are
          marked stale. A revision never overwrites: the old version stays and the change is logged with its reason.
        </li>
        <li>
          Estimates are ranges, never points, and each states its method and the figures it was computed from.
        </li>
        <li>
          Each comparison converts market prices into the model&apos;s unit (or the reverse) and prints its formula,
          its inputs with their sources, its assumptions with their ranges, and how far the result moves when each
          assumption is at either end. Market prices come from Hyperliquid, one venue.
        </li>
        <li>
          A gap is flagged when the model&apos;s range and the market&apos;s range have not overlapped for{" "}
          {d.stack?.gap_persist_days ?? "a set number of"} days in a row. A flag says the two disagree, not which is
          right.
        </li>
        <li>
          <Link href="/scenarios">Scenarios</Link> change one input and push it through the model to the revenue at
          risk at each company. Forecasts are published with a range and scored in the{" "}
          <Link href="/ledger">ledger</Link> when the actual figure is reported, misses included.
        </li>
      </ul>

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
