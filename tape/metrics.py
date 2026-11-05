"""The metrics job: cohorts, series, top positions, events, daily rows.

Runs after every sweep (the indexer launches it) and on a timer at :02 as a
catch-up. It computes every finished sweep hour that has no successful run
yet, oldest first, so a missed hour fills itself in.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import standing
from .cohorts import CohortRules, Position, SideFill, classify_bot, gross_by_address, median_hold_seconds, rank_whales
from .compute import COMPLEX, HourInputs, MarketCtx, Row, compute_hour, leg_subject, top_positions
from .events import Event, EventRules, market_events, position_events
from .graph import Graph, load_graph_file, write_graph
from .settings import Settings
from .timeutil import et_day, et_hour, hour_key, utcnow

log = logging.getLogger("tape.metrics")

CATCH_UP_LIMIT = 72


def pending_hours(conn, limit: int = CATCH_UP_LIMIT) -> list[datetime]:
    rows = conn.execute(
        """select s.hour from sweeps s
           left join runs r on r.job = 'metrics' and r.key = to_char(s.hour at time zone 'UTC', 'YYYY-MM-DD"T"HH24"Z"') and r.ok
           where s.finished_at is not null and r.job is null
           order by s.hour desc limit %s""",
        (limit,),
    ).fetchall()
    return sorted(r["hour"] for r in rows)


def load_ctxs(conn, hour: datetime) -> dict[str, MarketCtx]:
    rows = conn.execute(
        """select h.coin, m.leg, h.oi, h.mark, h.oracle, h.funding from market_hours h
           join markets m on m.coin = h.coin and m.active where h.hour = %s""",
        (hour,),
    ).fetchall()
    return {r["coin"]: MarketCtx(r["coin"], r["leg"], r["oi"], r["mark"], r["oracle"], r["funding"]) for r in rows}


def load_positions(conn, hour: datetime, coins: set[str]) -> list[Position]:
    rows = conn.execute("select address, coin, size from snapshots where hour = %s", (hour,)).fetchall()
    return [Position(r["address"], r["coin"], r["size"]) for r in rows if r["coin"] in coins]


def cohort_day(hour: datetime, recompute_hour_et: int) -> date:
    d = et_day(hour)
    return d if et_hour(hour) >= recompute_hour_et else d - timedelta(days=1)


def ensure_cohorts(conn, s: Settings, hour: datetime, positions: list[Position], ctxs: dict[str, MarketCtx]) -> date:
    """Whale and bot sets for the cohort day covering `hour`.

    A day's set is computed on the first hour at or after the recompute hour
    and then fixed for the day. Two exceptions keep the bootstrap honest:
    before any set exists the current hour seeds one, and a set computed while
    complex coverage was under the posting bar is recomputed every hour until
    coverage clears it (a whale set drawn from a fraction of the market is not
    the whale set)."""
    rules = CohortRules.from_config(s.t("cohorts"))
    recompute = int(s.t("cohorts", "recompute_hour_et"))
    floor = float(s.t("coverage", "post_min"))
    day = cohort_day(hour, recompute)
    have = conn.execute(
        "select meta from runs where job = 'cohorts' and key = %s and ok", (day.isoformat(),)
    ).fetchone()
    if have:
        meta = have["meta"] or {}
        cov, at = meta.get("coverage"), meta.get("hour")
        if cov is None or cov >= floor or at is None or hour_key(hour) <= at:
            return day
        compute_cohorts(conn, s, day, hour, positions, ctxs, rules)
        return day
    is_recompute_hour = et_day(hour) == day and et_hour(hour) >= recompute
    any_before = conn.execute("select max(day) as d from address_days where day < %s", (day,)).fetchone()["d"]
    if not is_recompute_hour and any_before is not None:
        return any_before
    compute_cohorts(conn, s, day, hour, positions, ctxs, rules)
    return day


def complex_coverage(positions: list[Position], ctxs: dict[str, MarketCtx]) -> float | None:
    oi = sum(m.oi_usd for m in ctxs.values())
    longs = sum(p.size * ctxs[p.coin].mark for p in positions if p.size > 0 and p.coin in ctxs)
    return longs / oi if oi > 0 else None


def compute_cohorts(
    conn,
    s: Settings,
    day: date,
    hour: datetime,
    positions: list[Position],
    ctxs: dict[str, MarketCtx],
    rules: CohortRules,
) -> None:
    lookback = timedelta(days=float(s.t("cohorts", "bot_lookback_days")))
    start = hour - lookback
    first_ws = conn.execute("select min(time) as t from fills where source = 'ws'").fetchone()["t"]
    observed_from = max(start, first_ws) if first_ws else hour
    days = (hour - observed_from).total_seconds() / 86400
    # Fills per day and hold times are meaningless over a few hours: any active
    # trader closes inside a short window. Until the tape has watched long
    # enough, nobody is classed a bot.
    min_days = float(s.t("cohorts", "bot_min_observed_days"))
    min_count = rules.bot_fills_per_day * days if days >= min_days else float("inf")
    cands = (
        conn.execute(
            """select addr, count(*) as n from (
             select buyer as addr from fills where time >= %(s)s and time < %(e)s and buyer is not null
             union all
             select seller from fills where time >= %(s)s and time < %(e)s and seller is not null) x
           group by addr having count(*) > %(m)s""",
            {"s": observed_from, "e": hour, "m": min_count},
        ).fetchall()
        if min_count != float("inf")
        else []
    )
    stats: dict[str, tuple[float, float | None, bool]] = {}
    for c in cands:
        a = c["addr"]
        fl = conn.execute(
            """select coin, extract(epoch from time) as t, case when buyer = %(a)s then sz else -sz end as d
               from fills where time >= %(s)s and time < %(e)s and (buyer = %(a)s or seller = %(a)s)""",
            {"a": a, "s": observed_from, "e": hour},
        ).fetchall()
        hold = median_hold_seconds(SideFill(r["coin"], float(r["t"]), float(r["d"])) for r in fl)
        per_day, bot = classify_bot(c["n"], days, hold, rules)
        stats[a] = (per_day, hold, bot)
    bots = {a for a, (_, _, b) in stats.items() if b}
    gross = gross_by_address(positions, {c: m.mark for c, m in ctxs.items()})
    whales = rank_whales(gross, bots, rules.whale_count)
    rank = {a: i for i, a in enumerate(whales, start=1)}
    rows = []
    for a in set(gross) | set(stats):
        per_day, hold, bot = stats.get(a, (None, None, False))
        rows.append((day, a, gross.get(a, 0.0), per_day, hold, bot, rank.get(a)))
    with conn.cursor() as cur:
        cur.execute("delete from address_days where day = %s", (day,))
        cur.executemany(
            """insert into address_days (day, address, gross_usd, fills_per_day, median_hold_s, is_bot, whale_rank)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )
        cur.execute(
            """insert into runs (job, key, started_at, finished_at, ok, note, meta)
               values ('cohorts', %s, now(), now(), true, %s, %s)
               on conflict (job, key) do update set finished_at = now(), ok = true, note = excluded.note,
               meta = excluded.meta""",
            (
                day.isoformat(),
                f"from snapshot {hour_key(hour)}; {len(whales)} whales, {len(bots)} bots",
                json.dumps({"hour": hour_key(hour), "coverage": complex_coverage(positions, ctxs)}),
            ),
        )
    log.info(
        "cohorts for %s from %s: %d whales, %d bots, %d addresses",
        day,
        hour_key(hour),
        len(whales),
        len(bots),
        len(rows),
    )


def is_warm(conn, s: Settings, hour: datetime) -> bool:
    first = conn.execute("select min(finished_at) as t from sweeps where finished_at is not null").fetchone()["t"]
    if first is None:
        return False
    return hour >= first + timedelta(hours=float(s.t("events", "warmup_hours")))


def snapshot_events(
    conn, s: Settings, hour: datetime, positions: list[Position], ctxs: dict[str, MarketCtx], whales: set[str]
) -> list[Event]:
    """Whale flips and entries between the previous sweep and this one, for
    addresses that were in both (their previous position is known)."""
    prev_hour = conn.execute(
        "select max(hour) as h from sweeps where finished_at is not null and hour < %s", (hour,)
    ).fetchone()["h"]
    if prev_hour is None or hour - prev_hour > timedelta(hours=3):
        return []
    rules = EventRules.from_config(s.t("events"))
    known = {
        (r["address"], r["dex"])
        for r in conn.execute("select address, dex from address_dexes where first_swept_hour <= %s", (prev_hour,))
    }
    dex_of = {c: c.split(":")[0] for c in ctxs}
    prev = {
        (r["address"], r["coin"]): r["size"]
        for r in conn.execute("select address, coin, size from snapshots where hour = %s", (prev_hour,))
    }
    cur = {(p.address, p.coin): p.size for p in positions}
    out: list[Event] = []
    day = et_day(hour).isoformat()
    for a, c in set(prev) | set(cur):
        if c not in ctxs or (a, dex_of[c]) not in known:
            continue
        out += position_events(
            time=hour,
            et_day=day,
            coin=c,
            address=a,
            prev_size=prev.get((a, c), 0.0),
            new_size=cur.get((a, c), 0.0),
            mark=ctxs[c].mark,
            is_whale=a in whales,
            rules=rules,
        )
    return out


def insert_events(conn, evs: list[Event]) -> int:
    n = 0
    with conn.cursor() as cur:
        for e in evs:
            cur.execute(
                """insert into events (time, coin, kind, address, size_usd, detail, dedupe_key, chart_hour)
                   values (%s, %s, %s, %s, %s, %s, %s, date_trunc('hour', %s::timestamptz))
                   on conflict (dedupe_key) do nothing""",
                (e.time, e.coin, e.kind, e.address, e.size_usd, json.dumps(e.detail), e.dedupe_key, e.time),
            )
            n += cur.rowcount
    return n


def update_position_since(conn, hour: datetime, positions: list[Position]) -> dict[tuple[str, str], datetime]:
    old = {
        (r["address"], r["coin"]): (r["sign"], r["since_hour"])
        for r in conn.execute("select address, coin, sign, since_hour from position_since")
    }
    new: dict[tuple[str, str], tuple[int, datetime]] = {}
    for p in positions:
        sign = 1 if p.size > 0 else -1
        o = old.get((p.address, p.coin))
        new[(p.address, p.coin)] = (sign, o[1] if o and o[0] == sign and o[1] <= hour else hour)
    with conn.cursor() as cur:
        cur.execute("delete from position_since")
        with cur.copy("copy position_since (address, coin, sign, since_hour) from stdin") as cp:
            for (a, c), (sg, since) in new.items():
                cp.write_row((a, c, sg, since))
    return {k: v[1] for k, v in new.items()}


def publish_definitions(conn, s: Settings) -> None:
    defs = {
        "cohorts": s.t("cohorts"),
        "coverage": s.t("coverage"),
        "events": s.t("events"),
        "daily": s.t("daily"),
        "handle": s.t("handle"),
        "stress": s.t("stress"),
        "loop": s.t("loop"),
        "depth": s.t("depth"),
        "legs": {k: {"label": v.label, "markets": list(v.markets)} for k, v in s.universe.legs.items()},
        "leg_order": list(s.universe.leg_order),
    }
    with conn.cursor() as cur:
        for k, v in defs.items():
            cur.execute(
                """insert into definitions (key, value, updated_at) values (%s, %s, now())
                   on conflict (key) do update set value = excluded.value, updated_at = now()""",
                (k, json.dumps(v)),
            )


def run_hour(conn, s: Settings, hour: datetime, graph: Graph | None = None) -> list[Row]:
    rules = CohortRules.from_config(s.t("cohorts"))
    ctxs = load_ctxs(conn, hour)
    positions = load_positions(conn, hour, set(ctxs))
    day = ensure_cohorts(conn, s, hour, positions, ctxs)
    ad = conn.execute("select address, is_bot, whale_rank from address_days where day = %s", (day,)).fetchall()
    whales = {r["address"] for r in ad if r["whale_rank"] is not None}
    bots = {r["address"] for r in ad if r["is_bot"]}
    held = {p.address for p in positions}
    fresh = {
        r["address"]
        for r in conn.execute(
            "select address from addresses where first_fill_complete and first_fill >= %s and address = any(%s)",
            (hour - timedelta(days=rules.fresh_days), list(held)),
        )
    }
    liq: dict[str, float] = defaultdict(float)
    for r in conn.execute(
        "select coin, sum(notional) as n from liquidations where time >= %s and time < %s group by coin",
        (hour - timedelta(hours=1), hour),
    ):
        liq[r["coin"]] = r["n"]
    prev = {
        r["subject"]: r["value"]
        for r in conn.execute(
            "select subject, value from series where metric = 'whale_net' and hour = %s and value is not null",
            (hour - timedelta(hours=24),),
        )
    }
    inp = HourInputs(
        ctxs=ctxs,
        positions=positions,
        whales=whales,
        bots=bots,
        fresh=fresh,
        liq_usd=liq,
        whale_net_24h_ago=prev,
        rules=rules,
        crowding_top_n=int(s.t("cohorts", "crowding_top_n")),
    )
    rows = compute_hour(inp)

    with conn.cursor() as cur:
        cur.execute("delete from series where hour = %s", (hour,))
        with cur.copy("copy series (subject, metric, hour, value, coverage) from stdin") as cp:
            for r in rows:
                cp.write_row((r.subject, r.metric, hour, r.value, r.coverage))

    since = update_position_since(conn, hour, positions)
    tops = top_positions(inp, n=int(s.t("cohorts", "whale_count")))
    with conn.cursor() as cur:
        cur.execute("delete from top_positions where hour = %s or hour < %s", (hour, hour - timedelta(days=2)))
        with cur.copy(
            "copy top_positions (coin, hour, rank, address, size, notional, cohort, fresh, since_hour) from stdin"
        ) as cp:
            for t in tops:
                cp.write_row(
                    (
                        t.coin,
                        hour,
                        t.rank,
                        t.address,
                        t.size,
                        t.notional,
                        t.cohort,
                        t.fresh,
                        since.get((t.address, t.coin)),
                    )
                )

    if is_warm(conn, s, hour):
        evs = snapshot_events(conn, s, hour, positions, ctxs, whales)
        er = EventRules.from_config(s.t("events"))
        by_coin: dict[str, dict[str, float | None]] = defaultdict(dict)
        for r in rows:
            if r.subject in ctxs:
                by_coin[r.subject][r.metric] = r.value
        for coin, vals in by_coin.items():
            evs += market_events(
                time=hour, hour_key=hour_key(hour), et_day=et_day(hour).isoformat(), coin=coin, values=vals, rules=er
            )
        n = insert_events(conn, evs)
        if n:
            log.info("%s: %d new events", hour_key(hour), n)

    if et_hour(hour) == int(s.t("daily", "snapshot_hour_et")):
        write_daily(conn, s, hour, rows)

    # The standing series are daily: the 00:00 UTC hour closes the day.
    stats, loop_events = standing.run(conn, s, hour, graph)
    if stats:
        with conn.cursor() as cur:
            cur.executemany(
                "insert into series (subject, metric, hour, value, coverage) values (%s, %s, %s, %s, null)",
                [(x.subject, x.metric, hour, x.value) for x in stats],
            )
    if loop_events and is_warm(conn, s, hour):
        n = insert_events(conn, loop_events)
        if n:
            log.info("%s: %d standing-series events", hour_key(hour), n)
    return rows


def write_daily(conn, s: Settings, hour: datetime, rows: list[Row]) -> None:
    """Daily rows only for subjects whose coverage clears the posting bar."""
    floor = float(s.t("coverage", "post_min"))
    day = et_day(hour)
    keep = [r for r in rows if r.coverage is not None and r.coverage >= floor]
    with conn.cursor() as cur:
        cur.execute("delete from daily_series where day = %s", (day,))
        cur.executemany(
            "insert into daily_series (day, subject, metric, hour, value, coverage) values (%s, %s, %s, %s, %s, %s)",
            [(day, r.subject, r.metric, hour, r.value, r.coverage) for r in keep],
        )
    skipped = sorted({r.subject for r in rows} - {r.subject for r in keep})
    log.info("daily rows for %s: %d written; under coverage: %s", day, len(keep), skipped or "none")


METRICS_LOCK = 0x7A9E_0001  # advisory lock id: one metrics job at a time (timer and post-sweep can overlap)


def run(conn, s: Settings, hours: list[datetime] | None = None, render: bool = True) -> list[datetime]:
    got = conn.execute("select pg_try_advisory_lock(%s) as ok", (METRICS_LOCK,)).fetchone()["ok"]
    conn.commit()
    if not got:
        log.info("another metrics job is running; exiting")
        return []
    try:
        return _run(conn, s, hours, render)
    finally:
        conn.execute("select pg_advisory_unlock(%s)", (METRICS_LOCK,))
        conn.commit()


def load_and_write_graph(conn, s: Settings) -> Graph | None:
    """The financing graph from config/graph.yaml into nodes and edges. A bad
    file is logged and skipped: it must not stop the positioning metrics."""
    try:
        g = load_graph_file()
        write_graph(conn, g, utcnow().date(), int(s.t("graph", "stale_days")))
        conn.commit()
        return g
    except Exception:
        conn.rollback()
        log.exception("graph load failed; the graph-based series are skipped")
        return None


def _run(conn, s: Settings, hours: list[datetime] | None, render: bool) -> list[datetime]:
    publish_definitions(conn, s)
    conn.commit()
    graph = load_and_write_graph(conn, s)
    hours = hours if hours is not None else pending_hours(conn)
    done: list[datetime] = []
    for h in hours:
        key = hour_key(h)
        conn.execute(
            """insert into runs (job, key, started_at) values ('metrics', %s, now())
               on conflict (job, key) do update set started_at = now(), finished_at = null, ok = null""",
            (key,),
        )
        conn.commit()
        try:
            rows = run_hour(conn, s, h, graph)
            conn.execute(
                "update runs set finished_at = now(), ok = true, note = %s where job = 'metrics' and key = %s",
                (f"{len(rows)} rows", key),
            )
            conn.commit()
            done.append(h)
            cx = {r.metric: r.value for r in rows if r.subject == COMPLEX}
            log.info("%s: complex whale net %s, coverage %s", key, _pct(cx.get("whale_net")), _pct(cx.get("coverage")))
        except Exception as e:
            conn.rollback()
            conn.execute(
                "update runs set finished_at = now(), ok = false, note = %s where job = 'metrics' and key = %s",
                (repr(e)[:500], key),
            )
            conn.commit()
            log.exception("metrics for %s failed", key)
    if render and done:
        from .charts import render_hour

        try:
            render_hour(conn, s, done[-1])
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("render for %s failed", hour_key(done[-1]))
    return done


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def latest_hour(conn) -> datetime | None:
    return conn.execute(
        "select max(hour) as h from series where subject = %s and metric = 'whale_net'", (COMPLEX,)
    ).fetchone()["h"]


__all__ = ["run", "run_hour", "latest_hour", "leg_subject", "utcnow"]
