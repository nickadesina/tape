"""The standing series against Postgres: daily closes in, series rows, deal
studies and extreme-event alerts out. The math is in tape/loop.py.

Runs inside the hourly metrics job, on the 00:00 UTC hour only: that hour's
marks close the previous UTC day, and the series are daily.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import loop
from .events import DEAL_STUDY, Event
from .graph import Graph
from .settings import Settings

log = logging.getLogger("tape.standing")


def load_closes(conn, coins: list[str] | None = None, since: date | None = None) -> loop.Closes:
    rows = conn.execute(
        """select coin, day, close from candles
           where (%s::text[] is null or coin = any(%s::text[])) and (%s::date is null or day >= %s::date)
           order by day""",
        (coins, coins, since, since),
    ).fetchall()
    out: loop.Closes = defaultdict(dict)
    for r in rows:
        out[r["coin"]][r["day"]] = r["close"]
    return dict(out)


def write_mark_candles(conn, hour: datetime) -> int:
    """The 00:00 UTC marks close the previous UTC day. A backfilled candle for
    that day wins over a mark."""
    if hour.hour != 0:
        return 0
    day = (hour - timedelta(days=1)).date()
    cur = conn.execute(
        """insert into candles (coin, day, close, source)
           select coin, %s, mark, 'mark' from market_hours where hour = %s
           on conflict (coin, day) do nothing""",
        (day, hour),
    )
    return cur.rowcount


def baskets(s: Settings) -> dict[str, list[str]]:
    legs = s.universe.legs
    return {k: [m for leg in v for m in legs[leg].markets] for k, v in s.t("loop", "baskets").items()}


def run(conn, s: Settings, hour: datetime, g: Graph | None) -> tuple[list[loop.Stat], list[Event]]:
    """Series rows for `hour` and the events they cross. Only on 00:00 UTC."""
    if hour.hour != 0:
        return [], []
    write_mark_candles(conn, hour)
    cfg = s.t("loop")
    day = (hour - timedelta(days=1)).date()
    closes = load_closes(conn)
    stats, extremes = loop.regime(closes, baskets(s), cfg["anchor"], day, cfg)
    events: list[Event] = []
    if g is not None:
        st, ex = loop.tension(closes, g, day, cfg)
        stats += st
        extremes += ex
        events += deal_studies(conn, g, closes, int(cfg["deal_days"]), hour)
    for x in extremes:
        if not _crossed(conn, x.subject, x.kind, day):
            continue
        events.append(
            Event(
                time=hour,
                coin=x.subject,
                kind=x.kind,
                dedupe_key=f"{x.kind}:{x.subject}:{day.isoformat()}",
                detail={**x.detail, **_names(g, x.detail), "day": day.isoformat()},
            )
        )
    return stats, events


def _crossed(conn, subject: str, kind: str, day: date) -> bool:
    """Alert when a series reaches an extreme, not every day it stays there:
    no alert of this kind for this subject in the last week."""
    r = conn.execute(
        "select 1 from events where kind = %s and coin = %s and time > %s limit 1",
        (kind, subject, day - timedelta(days=7)),
    ).fetchone()
    return r is None


def _names(g: Graph | None, d: dict) -> dict:
    if g is None:
        return {}
    out = {}
    for k in ("src", "dst"):
        if d.get(k) in g.nodes:
            out[f"{k}_name"] = g.nodes[d[k]].name
    return out


def deal_studies(conn, g: Graph, closes: loop.Closes, n: int, hour: datetime) -> list[Event]:
    """Upsert every verified dated edge's moves; an event for each deal whose
    n-day move is complete for the first time."""
    done = {
        r["edge"] for r in conn.execute("select distinct edge from deal_studies where day_n is not null").fetchall()
    }
    events: list[Event] = []
    for e in g.edges:
        moves = loop.deal_moves(closes, g, e, n)
        if not moves:
            continue
        with conn.cursor() as cur:
            cur.executemany(
                """insert into deal_studies (edge, coin, side, day0, day_n, circular) values (%s, %s, %s, %s, %s, %s)
                   on conflict (edge, coin) do update set day0 = excluded.day0, day_n = excluded.day_n,
                   circular = excluded.circular, computed_at = now()""",
                [(m.edge, m.coin, m.side, m.day0, m.day_n, m.circular) for m in moves],
            )
        complete = [m for m in moves if m.day_n is not None]
        # Only deals the tape watched happen: an old edge backfilled from candles is a study, not news.
        if complete and e.id not in done and e.date >= (hour - timedelta(days=n + 3)).date():
            dst = [m for m in complete if m.side == "dst"]
            src = [m for m in complete if m.side == "src"]
            events.append(
                Event(
                    time=hour,
                    coin=f"edge:{e.id}",
                    kind=DEAL_STUDY,
                    dedupe_key=f"{DEAL_STUDY}:{e.id}",
                    size_usd=e.size_usd,
                    detail={
                        "edge": e.id,
                        "type": e.type,
                        "date": e.date.isoformat(),
                        "days": n,
                        "circular": moves[0].circular,
                        "src_name": g.nodes[e.src].name,
                        "dst_name": g.nodes[e.dst].name,
                        "src_day0": _avg([m.day0 for m in src]),
                        "src_day_n": _avg([m.day_n for m in src]),
                        "dst_day0": _avg([m.day0 for m in dst]),
                        "dst_day_n": _avg([m.day_n for m in dst]),
                        "source": e.source,
                    },
                )
            )
    return events


def _avg(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None
