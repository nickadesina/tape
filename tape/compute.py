"""Every published number, computed for one hour. Pure: no I/O.

Definitions (also on the site's method page):

  whale net          (whale longs - whale shorts) / open interest, signed
  whale net change   whale net now minus whale net 24 hours earlier
  fresh share        gross notional of addresses under 30 days old / open interest
  crowding           gross notional of the top 5 addresses / open interest
  coverage           known long notional / open interest (must be >= 98% to post)
  funding            hourly funding rate as the API reports it (also annualized)
  premium            (mark - oracle) / oracle, basis points
  liquidation share  liquidated notional in the hour / open interest

Open interest is one side. Gross notional counts both sides of an address's
book, so fresh share and crowding can in principle exceed 100%.

Leg and complex values are open-interest-notional-weighted: every numerator and
denominator is summed in USD across the subject's markets, so a whale long NVDA
and short CRWV shows up at complex level as what it is, a spread.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .cohorts import BOT, MID, SMALL, WHALE, CohortRules, Position, cohort_of, gross_by_address

COMPLEX = "complex"
HOURS_PER_YEAR = 24 * 365

METRICS = (
    "whale_net",
    "whale_net_change",
    "mid_net",
    "small_net",
    "bot_net",
    "fresh_share",
    "crowding",
    "coverage",
    "imbalance",
    "funding",
    "funding_ann",
    "premium_bps",
    "liq_share",
    "oi_usd",
    "mark",
)


def leg_subject(leg: str) -> str:
    return f"leg:{leg}"


@dataclass(frozen=True)
class MarketCtx:
    coin: str
    leg: str
    oi: float  # coin units, one side
    mark: float
    oracle: float | None
    funding: float | None

    @property
    def oi_usd(self) -> float:
        return self.oi * self.mark


@dataclass
class Acc:
    """USD sums for one subject; each metric is a ratio of these."""

    oi_usd: float = 0.0
    known_long: float = 0.0
    known_short: float = 0.0
    net: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    fresh_gross: float = 0.0
    gross_by_addr: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    funding_w: float = 0.0
    premium_w: float = 0.0
    priced_oi: float = 0.0  # OI with an oracle price, the premium's weight
    funded_oi: float = 0.0
    liq: float = 0.0
    mark: float | None = None


@dataclass(frozen=True)
class Row:
    subject: str
    metric: str
    value: float | None
    coverage: float | None


@dataclass(frozen=True)
class HourInputs:
    ctxs: dict[str, MarketCtx]
    positions: list[Position]
    whales: set[str]
    bots: set[str]
    fresh: set[str]
    liq_usd: dict[str, float]  # coin -> liquidated notional in the hour
    whale_net_24h_ago: dict[str, float]  # subject -> whale net 24h before this hour
    rules: CohortRules
    crowding_top_n: int = 5


def _ratio(n: float, d: float) -> float | None:
    return n / d if d > 0 else None


def compute_hour(inp: HourInputs) -> list[Row]:
    marks = {c: m.mark for c, m in inp.ctxs.items()}
    gross_all = gross_by_address(inp.positions, marks)

    accs: dict[str, Acc] = defaultdict(Acc)

    def subjects(coin: str) -> tuple[str, ...]:
        return (coin, leg_subject(inp.ctxs[coin].leg), COMPLEX)

    for coin, m in inp.ctxs.items():
        if m.oi_usd <= 0:
            continue
        for s in subjects(coin):
            a = accs[s]
            a.oi_usd += m.oi_usd
            if m.funding is not None:
                a.funding_w += m.funding * m.oi_usd
                a.funded_oi += m.oi_usd
            if m.oracle:
                a.premium_w += (m.mark - m.oracle) / m.oracle * 1e4 * m.oi_usd
                a.priced_oi += m.oi_usd
            a.liq += inp.liq_usd.get(coin, 0.0)
        accs[coin].mark = m.mark

    for p in inp.positions:
        m = inp.ctxs.get(p.coin)
        if m is None or m.oi_usd <= 0 or p.size == 0:
            continue
        usd = p.size * m.mark
        cohort = cohort_of(p.address, gross_all.get(p.address, 0.0), inp.whales, inp.bots, inp.rules)
        for s in subjects(p.coin):
            a = accs[s]
            if usd > 0:
                a.known_long += usd
            else:
                a.known_short -= usd
            a.net[cohort] += usd
            a.gross_by_addr[p.address] += abs(usd)
            if p.address in inp.fresh:
                a.fresh_gross += abs(usd)

    rows: list[Row] = []
    for s, a in accs.items():
        if a.oi_usd <= 0:
            continue
        cov = a.known_long / a.oi_usd
        top = sorted(a.gross_by_addr.values(), reverse=True)[: inp.crowding_top_n]
        whale_net = a.net[WHALE] / a.oi_usd
        prev = inp.whale_net_24h_ago.get(s)
        funding = _ratio(a.funding_w, a.funded_oi)
        values: dict[str, float | None] = {
            "whale_net": whale_net,
            "whale_net_change": None if prev is None else whale_net - prev,
            "mid_net": a.net[MID] / a.oi_usd,
            "small_net": a.net[SMALL] / a.oi_usd,
            "bot_net": a.net[BOT] / a.oi_usd,
            "fresh_share": a.fresh_gross / a.oi_usd,
            "crowding": sum(top) / a.oi_usd,
            "coverage": cov,
            "imbalance": (a.known_long - a.known_short) / a.oi_usd,
            "funding": funding,
            "funding_ann": None if funding is None else funding * HOURS_PER_YEAR,
            "premium_bps": _ratio(a.premium_w, a.priced_oi),
            "liq_share": a.liq / a.oi_usd,
            "oi_usd": a.oi_usd,
            "mark": a.mark,
        }
        for metric, v in values.items():
            if metric == "mark" and v is None:
                continue
            rows.append(Row(s, metric, v, cov))
    rows.sort(key=lambda r: (r.subject, r.metric))
    return rows


@dataclass(frozen=True)
class TopPosition:
    coin: str
    rank: int
    address: str
    size: float
    notional: float
    cohort: str
    fresh: bool


def top_positions(inp: HourInputs, n: int = 50) -> list[TopPosition]:
    marks = {c: m.mark for c, m in inp.ctxs.items()}
    gross_all = gross_by_address(inp.positions, marks)
    by_coin: dict[str, list[Position]] = defaultdict(list)
    for p in inp.positions:
        if p.coin in inp.ctxs and p.size:
            by_coin[p.coin].append(p)
    out: list[TopPosition] = []
    for coin, ps in by_coin.items():
        mark = marks[coin]
        ps.sort(key=lambda p: (-abs(p.size), p.address))
        for i, p in enumerate(ps[:n], start=1):
            out.append(
                TopPosition(
                    coin=coin,
                    rank=i,
                    address=p.address,
                    size=p.size,
                    notional=abs(p.size) * mark,
                    cohort=cohort_of(p.address, gross_all.get(p.address, 0.0), inp.whales, inp.bots, inp.rules),
                    fresh=p.address in inp.fresh,
                )
            )
    return out


def rows_by(rows: Iterable[Row]) -> dict[tuple[str, str], Row]:
    return {(r.subject, r.metric): r for r in rows}
