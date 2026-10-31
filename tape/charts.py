"""Load series from Postgres and render every chart the site and posts use.

Kinds (charts.kind / subject):
  home   complex     the daily-post chart, live every hour (complex + legs, 30d)
  daily  complex     the 16:00 ET render that goes to Telegram for the post
  legs   all         small multiples, one panel per leg, same scale
  market coin@7d|@30d|@90d   stacked metric panels for the market page
  card   coin        one market, 7 days, the same panel as the daily chart
  event  <event id>  the card for the event's market with the trigger marked
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from .compute import COMPLEX, leg_subject
from .events import describe
from .render import (
    EXCEL,
    Bar,
    Callout,
    ChartSize,
    Footer,
    Line,
    Marker,
    Panel,
    render_card,
    render_grid,
    render_notice,
    render_panels,
)
from .settings import Settings
from .store import ChartStore
from .timeutil import ET, hour_key

log = logging.getLogger("tape.charts")

WINDOWS = (7, 30, 90)
MARKET_PANELS = (
    ("whale_net", "Whale net, % of OI", "pct"),
    ("fresh_share", "Fresh share (first fill < 30d), % of OI", "pct"),
    ("crowding", "Crowding: top 5 gross, % of OI", "pct"),
    ("funding_ann", "Funding, annualized", "pct_ann"),
    ("premium_bps", "Premium, mark vs oracle", "bps"),
    ("liq_share", "Liquidated in the hour, % of OI", "pct"),
)


def load_series(conn, subjects: list[str], metrics: list[str], start: datetime, end: datetime):
    """{(subject, metric): [(hour, value, coverage), ...]} ordered by hour."""
    out: dict[tuple[str, str], list] = defaultdict(list)
    for r in conn.execute(
        """select subject, metric, hour, value, coverage from series
           where subject = any(%s) and metric = any(%s) and hour > %s and hour <= %s order by hour""",
        (subjects, metrics, start, end),
    ):
        out[(r["subject"], r["metric"])].append((r["hour"], r["value"], r["coverage"]))
    return out


def legs_in_order(conn) -> list[tuple[str, str]]:
    rows = conn.execute(
        """select l.key, l.label from legs l where exists (select 1 from markets m where m.leg = l.key and m.active)
           order by l.ord"""
    ).fetchall()
    return [(r["key"], r["label"]) for r in rows]


def footer(s: Settings, hour: datetime, note: str | None = None) -> Footer:
    return Footer(handle=s.t("handle"), date_text=hour.astimezone(ET).strftime("%Y-%m-%d"), note=note)


def last_value(points) -> float | None:
    for _, v, _ in reversed(points):
        if v is not None:
            return v
    return None


def last_coverage(points) -> float | None:
    for _, v, c in reversed(points):
        if v is not None:
            return c
    return None


def home_chart(conn, s: Settings, hour: datetime) -> tuple[bytes, dict]:
    """The daily chart: complex and every leg, whale net over 30 days, a bar
    strip of today's 24h change per leg, and the biggest mover called out."""
    days = int(s.t("daily", "window_days"))
    thr = float(s.t("coverage", "dashed_below"))
    legs = legs_in_order(conn)
    subjects = [COMPLEX] + [leg_subject(k) for k, _ in legs]
    data = load_series(conn, subjects, ["whale_net", "whale_net_change"], hour - timedelta(days=days), hour)
    lines = [Line("AI complex", data[(COMPLEX, "whale_net")], HEAVY_COLOR, heavy=True)]
    bars = []
    changes: dict[str, float] = {}
    low_cov: list[str] = []
    for i, (k, label) in enumerate(legs):
        pts = data[(leg_subject(k), "whale_net")]
        if not pts:
            continue
        color = EXCEL[i % len(EXCEL)]
        cov = last_coverage(pts)
        disp = label if cov is None or cov >= thr else f"{label} (cov {cov * 100:.0f}%)"
        if cov is not None and cov < thr:
            low_cov.append(label)
        lines.append(Line(disp, pts, color))
        ch = last_value(data[(leg_subject(k), "whale_net_change")])
        bars.append(Bar(label.replace(" ", "\n", 1) if len(label) > 9 else label, ch, color))
        if ch is not None:
            changes[disp] = ch
    callout = None
    mover = None
    if changes:
        mover = max(changes, key=lambda k: abs(changes[k]))
        callout = Callout(mover, f"{mover.split(' (cov')[0]} {changes[mover] * 100:+.1f}pp")
    cx_cov = last_coverage(data[(COMPLEX, "whale_net")])
    note = None
    if low_cov:
        note = "dashed: coverage under 98% (" + ", ".join(low_cov) + ")"
    png = render_card(
        title="Whale net positioning, AI complex on Hyperliquid (% of open interest)",
        lines=lines, bars=bars or None, bars_title="Whale net change, last 24h",
        callout=callout, marker=None, footer=footer(s, hour, note), dashed_below=thr,
        size=ChartSize(int(s.t("render", "width_px")), int(s.t("render", "height_px"))),
    )
    info = {
        "complex_whale_net": last_value(data[(COMPLEX, "whale_net")]),
        "complex_change": last_value(data[(COMPLEX, "whale_net_change")]),
        "complex_coverage": cx_cov,
        "mover": mover.split(" (cov")[0] if mover else None,
        "mover_change": changes.get(mover) if mover else None,
    }
    return png, info


HEAVY_COLOR = "#000000"


def legs_chart(conn, s: Settings, hour: datetime) -> bytes:
    days = int(s.t("daily", "window_days"))
    thr = float(s.t("coverage", "dashed_below"))
    legs = legs_in_order(conn)
    data = load_series(conn, [leg_subject(k) for k, _ in legs], ["whale_net"], hour - timedelta(days=days), hour)
    panels = [(label, Line(label, data[(leg_subject(k), "whale_net")], EXCEL[i % len(EXCEL)]))
              for i, (k, label) in enumerate(legs)]
    return render_grid(title="Whale net by leg, % of open interest, 30 days", panels=panels,
                       footer=footer(s, hour), dashed_below=thr)


def market_chart(conn, s: Settings, coin: str, hour: datetime, days: int) -> bytes:
    thr = float(s.t("coverage", "dashed_below"))
    metrics = [m for m, _, _ in MARKET_PANELS] + ["mid_net", "small_net"]
    data = load_series(conn, [coin], metrics, hour - timedelta(days=days), hour)
    panels = []
    for m, title, fmt in MARKET_PANELS:
        lines = [Line("whales" if m == "whale_net" else title, data[(coin, m)], EXCEL[0])]
        if m == "whale_net":
            lines.append(Line("mid", data[(coin, "mid_net")], EXCEL[1]))
            lines.append(Line("small", data[(coin, "small_net")], EXCEL[2]))
        panels.append(Panel(title, lines, fmt))
    cov = last_coverage(data[(coin, "whale_net")])
    cov_txt = "no data" if cov is None else f"coverage {cov * 100:.1f}%"
    return render_panels(title=f"{coin}  ·  {days} days  ·  {cov_txt}", panels=panels,
                         footer=footer(s, hour), dashed_below=thr)


def card_chart(conn, s: Settings, coin: str, hour: datetime, marker: Marker | None = None, days: int = 7) -> bytes:
    """One market, the daily chart's panel zoomed to `days`."""
    thr = float(s.t("coverage", "dashed_below"))
    data = load_series(conn, [coin], ["whale_net", "mid_net", "small_net", "whale_net_change"],
                       hour - timedelta(days=days), hour)
    lines = [
        Line("whales", data[(coin, "whale_net")], HEAVY_COLOR, heavy=True),
        Line("mid", data[(coin, "mid_net")], EXCEL[0]),
        Line("small", data[(coin, "small_net")], EXCEL[1]),
    ]
    ch = last_value(data[(coin, "whale_net_change")])
    callout = Callout("whales", f"{ch * 100:+.1f}pp 24h") if ch is not None and marker is None else None
    cov = last_coverage(data[(coin, "whale_net")])
    note = None if cov is None or cov >= thr else f"coverage {cov * 100:.1f}%: dashed under 98%"
    return render_card(
        title=f"{coin}: net positioning by cohort, % of open interest, {days} days",
        lines=lines, bars=None, bars_title="", callout=callout, marker=marker,
        footer=footer(s, hour, note), dashed_below=thr,
        size=ChartSize(int(s.t("render", "width_px")), int(s.t("render", "height_px"))),
    )


def render_hour(conn, s: Settings, hour: datetime) -> int:
    """Every chart the site shows for `hour`. Returns how many were stored."""
    store = ChartStore(s.env)
    size = (int(s.t("render", "width_px")), int(s.t("render", "height_px")))
    n = 0
    png, _ = home_chart(conn, s, hour)
    store.put(conn, "home", COMPLEX, hour, png, size)
    store.put(conn, "legs", "all", hour, legs_chart(conn, s, hour), size)
    n += 2
    coins = [r["coin"] for r in conn.execute("select coin from markets where active order by coin")]
    for c in coins:
        has = conn.execute("select 1 from series where subject = %s and hour = %s limit 1", (c, hour)).fetchone()
        if not has:
            continue
        for d in WINDOWS:
            store.put(conn, "market", f"{c}@{d}d", hour, market_chart(conn, s, c, hour, d), (1200, 1000))
            n += 1
        store.put(conn, "card", c, hour, card_chart(conn, s, c, hour), size)
        n += 1
    prune(conn, s, hour)
    log.info("rendered %d charts for %s", n, hour_key(hour))
    return n


def prune(conn, s: Settings, hour: datetime) -> None:
    """With the db store, drop old hourly charts but keep every chart that a
    post can have linked to: daily and event charts, and the daily hour's."""
    if s.env.chart_store != "db":
        return
    keep_days = int(s.t("render", "hourly_retention_days"))
    daily_hour = int(s.t("daily", "snapshot_hour_et"))
    conn.execute(
        """delete from charts where kind in ('home', 'legs', 'card') and hour < %s
             and extract(hour from hour at time zone 'America/New_York') <> %s""",
        (hour - timedelta(days=keep_days), daily_hour),
    )
    # Market panels are the bulk; keep two days of them plus the daily hour.
    conn.execute(
        """delete from charts where kind = 'market' and hour < %s
             and extract(hour from hour at time zone 'America/New_York') <> %s""",
        (hour - timedelta(days=2), daily_hour),
    )


def render_event(conn, s: Settings, event: dict) -> tuple[bytes, datetime] | None:
    hour = conn.execute(
        "select max(hour) as h from series where subject = %s and hour <= %s",
        (event["coin"], event["time"] + timedelta(hours=1)),
    ).fetchone()["h"]
    if hour is None:
        return None
    text = describe(event["kind"], event["coin"], event["size_usd"], event["detail"])
    png = card_chart(conn, s, event["coin"], hour, marker=Marker(event["time"], text))
    ChartStore(s.env).put(conn, "event", str(event["id"]), hour, png,
                          (int(s.t("render", "width_px")), int(s.t("render", "height_px"))))
    return png, hour


def notice_chart(s: Settings, hour: datetime, text: str) -> bytes:
    return render_notice(text=text, footer=footer(s, hour))
