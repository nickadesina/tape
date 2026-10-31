"""Send pending event alerts to Telegram, each with its chart.

Alerts carry the market, the size, the address and an explorer link. They
never name a person: the address is what it is. The post to X is written by
hand from the alert; `tape event-posted <id> <url>` records it so the events
page links to it.
"""

from __future__ import annotations

import logging

from .db import connect
from .events import describe
from .settings import Settings
from .telegram import Telegram
from .timeutil import ET, et_day, et_hour_on, hour_key, utcnow

log = logging.getLogger("tape.alerts")
EXPLORER = "https://app.hyperliquid.xyz/explorer/address/"


def slug(coin: str) -> str:
    return coin.replace(":", "-")


def alert_text(s: Settings, e: dict, posts_today: int, chart_hour) -> str:
    lines = [describe(e["kind"], e["coin"], e["size_usd"], e["detail"])]
    lines.append(e["time"].astimezone(ET).strftime("%Y-%m-%d %H:%M ET"))
    cov = (e["detail"] or {}).get("coverage")
    if cov is not None:
        lines.append(f"coverage {cov * 100:.1f}%")
    if e["address"]:
        lines.append(f"{e['address']}\n{EXPLORER}{e['address']}")
    site = s.env.site_url
    if chart_hour is not None:
        lines.append(f"{site}/p/event/{e['id']}/{hour_key(chart_hour)}")
    lines.append(f"{site}/m/{slug(e['coin'])}")
    lines.append(f"event posts today: {posts_today}/2  ·  record: tape event-posted {e['id']} <url>")
    return "\n".join(lines)


def send_pending(s: Settings, limit: int = 10) -> int:
    from .charts import render_event

    tg = Telegram(s.env)
    sent = 0
    with connect(s.env.database_url) as conn:
        rows = conn.execute(
            """select * from events where alerted_at is null order by time limit %s for update skip locked""",
            (limit,),
        ).fetchall()
        day_start = et_hour_on(et_day(utcnow()), 0)
        for e in rows:
            posts_today = conn.execute(
                "select count(*) as n from events where post_url is not null and alerted_at >= %s", (day_start,)
            ).fetchone()["n"]
            chart = None
            try:
                chart = render_event(conn, s, e)
            except Exception:
                log.exception("event chart for %s failed", e["id"])
            text = alert_text(s, e, posts_today, chart[1] if chart else None)
            try:
                if chart:
                    tg.photo(chart[0], text)
                else:
                    tg.message(text)
            except Exception:
                log.exception("telegram send for event %s failed", e["id"])
                continue
            conn.execute("update events set alerted_at = now() where id = %s", (e["id"],))
            sent += 1
        conn.commit()
    return sent


def record_post(s: Settings, event_id: int, url: str) -> None:
    with connect(s.env.database_url) as conn:
        n = conn.execute("update events set post_url = %s where id = %s", (url, event_id)).rowcount
        conn.commit()
    if not n:
        raise SystemExit(f"no event {event_id}")

