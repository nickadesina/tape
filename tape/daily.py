"""The 16:00 ET job: render the daily chart and send it to Telegram.

It renders from the daily snapshot hour (15:00 ET by default: the last sweep
that finishes before 16:00). If the complex-level coverage is under the
posting bar, it sends the gap-day line instead of a chart: a gap day beats a
wrong chart, and the gap is the post.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from .charts import home_chart
from .compute import COMPLEX, leg_subject
from .db import connect
from .settings import Settings
from .store import ChartStore
from .telegram import Telegram
from .timeutil import ET, et_day, et_hour_on, hour_key, utcnow

log = logging.getLogger("tape.daily")


def pick_hour(conn, s: Settings, day: date) -> tuple[datetime | None, bool]:
    """The daily snapshot hour if its metrics exist, else the latest computed
    hour before 16:00 ET that day. The flag says whether it is the scheduled one."""
    target = et_hour_on(day, int(s.t("daily", "snapshot_hour_et")))
    row = conn.execute(
        "select 1 from series where subject = %s and metric = 'whale_net' and hour = %s", (COMPLEX, target)
    ).fetchone()
    if row:
        return target, True
    fallback = conn.execute(
        "select max(hour) as h from series where subject = %s and metric = 'whale_net' and hour <= %s and hour >= %s",
        (COMPLEX, et_hour_on(day, 16), et_hour_on(day, 0)),
    ).fetchone()["h"]
    return fallback, False


def low_coverage_markets(conn, hour: datetime, floor: float) -> list[tuple[str, float]]:
    rows = conn.execute(
        """select subject, value from series where hour = %s and metric = 'coverage'
           and subject not like 'leg:%%' and subject <> 'complex' and value < %s order by value""",
        (hour, floor),
    ).fetchall()
    return [(r["subject"], r["value"]) for r in rows]


def biggest_market_mover(conn, hour: datetime) -> tuple[str, float] | None:
    r = conn.execute(
        """select subject, value from series where hour = %s and metric = 'whale_net_change'
           and subject not like 'leg:%%' and subject <> 'complex' and value is not null
           order by abs(value) desc limit 1""",
        (hour,),
    ).fetchone()
    return (r["subject"], r["value"]) if r else None


def caption(s: Settings, day: date, hour: datetime, info: dict, market_mover, permalink: str, scheduled: bool) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x * 100:+.1f}%"

    def pp(x):
        return "n/a" if x is None else f"{x * 100:+.1f}pp"

    lines = [f"Tape {day.isoformat()}  ·  as of {hour.astimezone(ET).strftime('%H:%M ET')}"
             + ("" if scheduled else " (late: scheduled sweep missing)")]
    lines.append(f"Complex whale net {pct(info['complex_whale_net'])} of OI, 24h {pp(info['complex_change'])}")
    if info.get("mover"):
        lines.append(f"Biggest leg move: {info['mover']} {pp(info['mover_change'])}")
    if market_mover:
        lines.append(f"Biggest market move: {market_mover[0]} {pp(market_mover[1])}")
    cov = info.get("complex_coverage")
    lines.append(f"Coverage {'n/a' if cov is None else f'{cov * 100:.1f}%'}")
    lines.append(permalink)
    return "\n".join(lines)


def run(s: Settings, day: date | None = None, send: bool = True) -> dict:
    day = day or et_day(utcnow())
    tg = Telegram(s.env)
    floor = float(s.t("coverage", "post_min"))
    with connect(s.env.database_url) as conn:
        hour, scheduled = pick_hour(conn, s, day)
        if hour is None:
            text = f"Tape {day}: no metrics today. The indexer is behind or down; no post."
            if send:
                tg.message(text)
            _record(conn, day, False, text)
            return {"posted": False, "reason": "no metrics"}
        png, info = home_chart(conn, s, hour)
        cov = info.get("complex_coverage")
        if cov is None or cov < floor:
            low = low_coverage_markets(conn, hour, floor)
            worst = ", ".join(f"{c} {v * 100:.0f}%" for c, v in low[:6]) or "n/a"
            text = (
                f"Tape {day}: GAP DAY. Complex coverage {('n/a' if cov is None else f'{cov * 100:.1f}%')} "
                f"is under {floor * 100:.0f}%, so no chart. Lowest: {worst}.\n"
                f"Suggested post: the indexer is behind on {len(low)} market(s); back tomorrow."
            )
            if send:
                tg.message(text)
            _record(conn, day, False, text)
            return {"posted": False, "reason": "coverage", "coverage": cov}
        store = ChartStore(s.env)
        size = (int(s.t("render", "width_px")), int(s.t("render", "height_px")))
        store.put(conn, "daily", COMPLEX, hour, png, size)
        link = f"{s.env.site_url}/p/daily/complex/{hour_key(hour)}"
        cap = caption(s, day, hour, info, biggest_market_mover(conn, hour), link, scheduled)
        if send:
            tg.photo(png, cap)
        _record(conn, day, True, cap)
        conn.commit()
        return {"posted": True, "hour": hour_key(hour), "caption": cap}


def _record(conn, day: date, ok: bool, note: str) -> None:
    conn.execute(
        """insert into runs (job, key, started_at, finished_at, ok, note) values ('daily', %s, now(), now(), %s, %s)
           on conflict (job, key) do update set finished_at = now(), ok = excluded.ok, note = excluded.note""",
        (day.isoformat(), ok, note),
    )
    conn.commit()


__all__ = ["run", "leg_subject"]
