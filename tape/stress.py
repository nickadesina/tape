"""The stress test on real positions. Pure: positions, margin and books in,
forced selling out.

One run takes a sweep (every position, its leverage and liquidation price,
and each account's margin), same-hour order books for each market, and a
shock, then:

  1. Moves the shocked markets by the shock. A graph shock also moves linked
     markets by their measured co-movement.
  2. Recomputes every account's equity and maintenance margin: per account
     for cross margin, per position for isolated.
  3. Marks accounts below maintenance as liquidated and turns their positions
     into forced market orders.
  4. Walks each market's book with the forced flow to get the new price.
  5. Repeats from 2 until no new account breaches, or `max_rounds`.
  6. Reports forced notional by market and round, the price path, the number
     of accounts hit (never which), and the amplification: the shocked
     markets' final drawdown over the initial shock.

Modelling choices, each one a simplification the replay validation checks:

  - Cross margin on a HIP-3 dex is per dex. An account's equity at new prices
    is its cross account value plus the PnL of its complex positions from
    their snapshot marks; its maintenance is the stored cross maintenance plus
    the change in its complex positions' maintenance. Positions outside the
    complex are held at their snapshot price.
  - Maintenance margin rate is 1 / (2 x max leverage), Hyperliquid's rule
    without margin tiers.
  - An isolated position is liquidated when price crosses its stored
    liquidation price. So is a cross position whose account has no margin
    row (the sweeps before account margin was stored).
  - A liquidated position is closed in full, at market. Hyperliquid
    liquidates large positions partially and its backstop vault can take
    what the book cannot; both are left out, so forced selling is an upper
    bound and "unabsorbed" is what the book could not take.
  - After the exogenous shock the book re-forms around the shocked price and
    does not refill between rounds: cumulative forced flow walks one book.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .depth import scale_profile, walk

CROSS, ISOLATED = "cross", "isolated"


@dataclass(frozen=True)
class Pos:
    coin: str
    size: float  # signed, coin units
    px0: float  # mark the position was valued at in the sweep
    margin_type: str | None  # cross | isolated; None is treated as cross
    liq_px: float | None
    margin_used: float | None = None  # isolated: margin plus unrealized PnL at px0
    mm_rate: float = 0.05


@dataclass(frozen=True)
class Account:
    address: str
    dex: str
    positions: tuple[Pos, ...]
    cross_value: float | None = None  # None: no margin row; cross positions fall back to liq_px
    cross_mm: float | None = None


@dataclass(frozen=True)
class Book:
    """Cumulative resting size by distance from mid, each side (depth.profile)."""

    bid: list[tuple[float, float]]
    ask: list[tuple[float, float]]

    def scaled(self, k: float) -> Book:
        return Book(scale_profile(self.bid, k), scale_profile(self.ask, k))


@dataclass(frozen=True)
class Shock:
    key: str  # e.g. node:anthropic@-20
    label: str  # e.g. "Anthropic -20%"
    kind: str  # node | graph | replay
    moves: dict[str, float]  # market -> initial exogenous return, e.g. -0.2
    shocked: tuple[str, ...]  # markets whose drawdown defines amplification
    shock: float  # the headline shock, e.g. -0.2


@dataclass
class MarketPath:
    coin: str
    ref: float
    initial: float  # price after the exogenous move
    prices: list[float] = field(default_factory=list)  # after each round
    forced_usd: list[float] = field(default_factory=list)  # forced notional each round
    unabsorbed_usd: float = 0.0

    @property
    def final(self) -> float:
        return self.prices[-1] if self.prices else self.initial

    def as_dict(self) -> dict:
        return {
            "ref": self.ref,
            "initial": self.initial,
            "prices": self.prices,
            "forced_usd": self.forced_usd,
            "unabsorbed_usd": self.unabsorbed_usd,
            "drawdown": self.final / self.ref - 1 if self.ref else None,
        }


@dataclass
class Result:
    shock: Shock
    rounds: int
    accounts_hit: int
    positions_hit: int
    forced_usd: float
    unabsorbed_usd: float
    amplification: float | None
    markets: dict[str, MarketPath]

    @property
    def markets_hit(self) -> int:
        return sum(1 for m in self.markets.values() if sum(m.forced_usd) > 0)

    def detail(self) -> dict:
        return {
            c: m.as_dict() for c, m in sorted(self.markets.items()) if sum(m.forced_usd) > 0 or c in self.shock.moves
        }


def mm_rate(max_leverage: float | None) -> float:
    """Hyperliquid: maintenance margin is half the initial margin at max leverage."""
    return 1 / (2 * max_leverage) if max_leverage and max_leverage > 0 else 0.05


def _breached(p: Pos, price: float) -> bool:
    if p.liq_px:
        return price <= p.liq_px if p.size > 0 else price >= p.liq_px
    if p.margin_type == ISOLATED and p.margin_used is not None:
        eq = p.margin_used + p.size * (price - p.px0)
        return eq < abs(p.size) * price * p.mm_rate
    return False


def _cross_breached(a: Account, cross: list[Pos], prices: dict[str, float]) -> bool:
    eq = a.cross_value + sum(p.size * (prices.get(p.coin, p.px0) - p.px0) for p in cross)
    mm = (a.cross_mm or 0.0) + sum(abs(p.size) * (prices.get(p.coin, p.px0) - p.px0) * p.mm_rate for p in cross)
    return eq < mm


def run(
    accounts: Iterable[Account],
    refs: dict[str, float],
    books: dict[str, Book],
    shock: Shock,
    max_rounds: int = 10,
    walk_books: bool = True,
) -> Result:
    """One scenario. `refs` is each market's mark at the sweep. With
    `walk_books=False` prices stay at the shocked level (the replay: the
    realized move already contains whatever the liquidations did to price)."""
    accounts = [
        Account(a.address, a.dex, tuple(p for p in a.positions if p.coin in refs), a.cross_value, a.cross_mm)
        for a in accounts
    ]
    initial = {c: r * (1 + shock.moves.get(c, 0.0)) for c, r in refs.items()}
    prices = dict(initial)
    paths = {c: MarketPath(c, refs[c], initial[c]) for c in refs}
    cum_flow: dict[str, float] = defaultdict(float)  # coin units, positive = net forced selling
    gone_cross: set[int] = set()
    gone_pos: set[tuple[int, int]] = set()
    hit_accounts: set[int] = set()
    positions_hit = 0
    rounds = 0
    for _ in range(max_rounds):
        orders: dict[str, float] = defaultdict(float)
        for i, a in enumerate(accounts):
            margin_cross = [
                (j, p) for j, p in enumerate(a.positions) if p.margin_type != ISOLATED and a.cross_value is not None
            ]
            if margin_cross and i not in gone_cross:
                if _cross_breached(a, [p for _, p in margin_cross], prices):
                    gone_cross.add(i)
                    hit_accounts.add(i)
                    for j, p in margin_cross:
                        gone_pos.add((i, j))
                        orders[p.coin] += p.size
                        positions_hit += 1
            for j, p in enumerate(a.positions):
                if (i, j) in gone_pos or (p.margin_type != ISOLATED and a.cross_value is not None):
                    continue
                if p.coin in prices and _breached(p, prices[p.coin]):
                    gone_pos.add((i, j))
                    hit_accounts.add(i)
                    orders[p.coin] += p.size
                    positions_hit += 1
        if not orders:
            break
        rounds += 1
        before = dict(prices)
        for c, q in orders.items():
            cum_flow[c] += q
            if walk_books and c in books:
                net = cum_flow[c]
                if net > 0:
                    d, left = walk(books[c].bid, net)
                    prices[c] = initial[c] * (1 - min(d, 0.999))
                elif net < 0:
                    d, left = walk(books[c].ask, -net)
                    prices[c] = initial[c] * (1 + d)
                else:
                    d, left = 0.0, 0.0
                    prices[c] = initial[c]
                paths[c].unabsorbed_usd = left * prices[c]
        for c, path in paths.items():
            q = orders.get(c, 0.0)
            path.prices.append(prices[c])
            path.forced_usd.append(abs(q) * (before[c] + prices[c]) / 2)
    amp = None
    if shock.shocked and shock.shock:
        dd = [paths[c].final / paths[c].ref - 1 for c in shock.shocked if c in paths and paths[c].ref]
        if dd:
            amp = (sum(dd) / len(dd)) / shock.shock
    return Result(
        shock=shock,
        rounds=rounds,
        accounts_hit=len(hit_accounts),
        positions_hit=positions_hit,
        forced_usd=sum(sum(p.forced_usd) for p in paths.values()),
        unabsorbed_usd=sum(p.unabsorbed_usd for p in paths.values()),
        amplification=amp,
        markets=paths,
    )


def node_shock(node_key: str, label: str, markets: Iterable[str], shock: float) -> Shock:
    ms = tuple(sorted(markets))
    return Shock(
        key=f"node:{node_key}@{round(shock * 100)}",
        label=f"{label} {shock * 100:+.0f}%",
        kind="node",
        moves={c: shock for c in ms},
        shocked=ms,
        shock=shock,
    )


def graph_shock(node_key: str, label: str, markets: Iterable[str], shock: float, linked: dict[str, float]) -> Shock:
    """`linked` maps each dependent market to its measured co-movement (beta) with the node."""
    ms = tuple(sorted(markets))
    moves = {c: max(-0.95, beta * shock) for c, beta in linked.items() if c not in ms}
    moves.update({c: shock for c in ms})
    return Shock(
        key=f"graph:{node_key}@{round(shock * 100)}",
        label=f"{label} and its dependents {shock * 100:+.0f}%",
        kind="graph",
        moves=moves,
        shocked=ms,
        shock=shock,
    )


def run_range(
    accounts: list[Account],
    refs: dict[str, float],
    books: dict[str, Book],
    scales: dict[str, tuple[float, float, float]],
    shock: Shock,
    max_rounds: int = 10,
) -> tuple[Result, Result, Result]:
    """The scenario on the thick, central and thin book: depth at this hour
    varies, so the result is a range. `scales` maps market to (thick, central,
    thin) multipliers on the stored book; a market without one keeps it as is."""
    out = []
    for i in range(3):
        b = {c: bk.scaled(scales[c][i]) if c in scales else bk for c, bk in books.items()}
        out.append(run(accounts, refs, b, shock, max_rounds))
    return out[0], out[1], out[2]


def within(predicted: float, recorded: float, tolerance: float) -> bool:
    return abs(predicted - recorded) <= tolerance * recorded


def find_drawdowns(
    marks: dict[str, list[tuple]], min_move: float, window_h: int
) -> list[tuple[str, object, object, float]]:
    """Episodes for the replay: for each market, hours whose mark fell by at
    least `min_move` within the next `window_h` hours. `marks` maps market to
    [(hour, mark), ...] in hour order. Returns (coin, start, trough hour, move),
    non-overlapping per market, earliest first."""
    out = []
    for coin, pts in marks.items():
        i = 0
        while i < len(pts):
            h0, m0 = pts[i]
            best_j, best = None, 0.0
            j = i + 1
            while j < len(pts) and (pts[j][0] - h0).total_seconds() <= window_h * 3600:
                mv = pts[j][1] / m0 - 1 if m0 else 0.0
                if mv < best:
                    best, best_j = mv, j
                j += 1
            if best_j is not None and best <= -min_move:
                out.append((coin, h0, pts[best_j][0], best))
                i = best_j + 1
            else:
                i += 1
    return sorted(out, key=lambda e: (e[1], e[0]))
