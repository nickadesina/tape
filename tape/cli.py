"""tape <command>. Run `tape -h` for the list."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import httpx

from . import settings as settings_mod

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def _settings():
    return settings_mod.load()


def cmd_migrate(a) -> None:
    from .db import migrate

    applied = migrate(_settings().env.database_url)
    print("applied:", applied or "nothing (up to date)")


def cmd_universe(a) -> None:
    from .hl import InfoClient, WeightBucket
    from .universe import resolve

    s = _settings()

    async def go():
        info = InfoClient(s.env.info_url, WeightBucket(1000))
        try:
            dexs = {d["name"] for d in await info.perp_dexs() if d}
            metas = {d: await info.meta_and_ctxs(d) for d in s.universe.dexes if d in dexs}
        finally:
            await info.close()
        return metas

    metas = asyncio.run(go())
    u = resolve(s.universe, metas)
    ctx = {}
    for resp in metas.values():
        for asset, c in zip(resp[0]["universe"], resp[1], strict=True):
            ctx[asset["name"]] = c
    if a.json:
        print(json.dumps({"markets": {c: m.leg for c, m in u.markets.items()}, "missing": u.missing,
                          "delisted": u.delisted, "candidates": u.candidates, "unclassified": u.unclassified}, indent=2))
        return
    for leg in s.universe.leg_order:
        print(f"{s.universe.legs[leg].label}:")
        for c in u.leg_markets(leg):
            x = ctx[c]
            oi_usd = float(x["openInterest"]) * float(x["markPx"])
            print(f"  {c:14s} max {u.markets[c].max_leverage:>2}x  OI ${oi_usd / 1e6:8.2f}M  "
                  f"24h vol ${float(x['dayNtlVlm']) / 1e6:8.2f}M")
    print("missing on chain:", u.missing or "none")
    print("delisted:", u.delisted or "none")
    print("candidates (live, not in a leg):", u.candidates or "none")
    print(f"unclassified live markets on {', '.join(s.universe.dexes)}: {len(u.unclassified)}")
    if a.all:
        print("  " + " ".join(u.unclassified))


def cmd_index(a) -> None:
    from .indexer import main

    asyncio.run(main(_settings()))


def cmd_metrics(a) -> None:
    from . import metrics
    from .db import connect
    from .timeutil import parse_hour_key

    s = _settings()
    with connect(s.env.database_url) as conn:
        hours = [parse_hour_key(h) for h in a.hour] if a.hour else None
        done = metrics.run(conn, s, hours, render=not a.no_render)
    print(f"computed {len(done)} hour(s)")


def cmd_render(a) -> None:
    from .charts import render_hour
    from .db import connect
    from .metrics import latest_hour
    from .timeutil import parse_hour_key

    s = _settings()
    with connect(s.env.database_url) as conn:
        hour = parse_hour_key(a.hour) if a.hour else latest_hour(conn)
        if hour is None:
            sys.exit("no series yet")
        n = render_hour(conn, s, hour)
        conn.commit()
    print(f"rendered {n} charts")


def cmd_daily(a) -> None:
    from datetime import date

    from . import daily

    out = daily.run(_settings(), date.fromisoformat(a.day) if a.day else None, send=not a.dry_run)
    print(json.dumps(out, indent=2, default=str))


def cmd_alerts(a) -> None:
    from .alerts import send_pending

    print(f"sent {send_pending(_settings())}")


def cmd_event_posted(a) -> None:
    from .alerts import record_post

    record_post(_settings(), a.id, a.url)
    print("recorded")


def cmd_match(a) -> None:
    from .match import match_text

    print(json.dumps(match_text(_settings(), " ".join(a.text)), indent=2))


def cmd_seed(a) -> None:
    from .db import connect

    s = _settings()
    r = httpx.get(LEADERBOARD_URL, timeout=120)
    r.raise_for_status()
    rows = [x for x in r.json()["leaderboardRows"] if float(x["accountValue"]) >= a.min_account_value]
    addrs = sorted({x["ethAddress"].lower() for x in rows})
    with connect(s.env.database_url) as conn, conn.cursor() as cur:
        cur.executemany(
            "insert into addresses (address, source) values (%s, 'leaderboard') on conflict do nothing",
            [(x,) for x in addrs],
        )
        cur.executemany(
            "insert into address_dexes (address, dex, seed_pending) values (%s, %s, true) on conflict do nothing",
            [(x, d) for x in addrs for d in s.universe.dexes],
        )
        conn.commit()
    print(f"queued {len(addrs)} leaderboard addresses (account value >= ${a.min_account_value:,.0f}) "
          f"on {len(s.universe.dexes)} dexes; the indexer snapshots them at low priority")


def cmd_status(a) -> None:
    from .db import connect

    s = _settings()
    with connect(s.env.database_url) as conn:
        q = lambda sql, *p: conn.execute(sql, p).fetchall()  # noqa: E731
        print("sweeps:")
        for r in q("select * from sweeps order by hour desc limit 5"):
            dur = (r["finished_at"] - r["started_at"]).total_seconds() if r["finished_at"] else None
            print(f"  {r['hour']:%Y-%m-%d %H:%M}Z  {r['addresses'] or 0:>6} swept  {r['positions'] or 0:>6} positions  "
                  f"{r['errors'] or 0} errors  {'%.0fs' % dur if dur else 'running'}")
        c = q("""select (select count(*) from addresses) a, (select count(*) from address_dexes where seed_pending) seeds,
                 (select count(*) from addresses where history_checked_at is null) hist,
                 (select count(*) from fills) fills, (select max(time) from fills) last_fill,
                 (select count(*) from events) events""")[0]
        print(f"addresses {c['a']}  seed queue {c['seeds']}  history queue {c['hist']}  fills {c['fills']}  "
              f"last fill {c['last_fill']}  events {c['events']}")
        h = q("select max(hour) h from series")[0]["h"]
        if h:
            print(f"coverage at {h:%Y-%m-%d %H:%M}Z:")
            for r in q("""select subject, value from series where hour = %s and metric = 'coverage'
                          order by subject = 'complex' desc, subject like 'leg:%%' desc, subject""", h):
                flag = "" if r["value"] >= float(s.t("coverage", "post_min")) else "  < post bar"
                print(f"  {r['subject']:22s} {r['value'] * 100:6.1f}%{flag}")
        for r in q("select job, key, ok, note from runs order by started_at desc limit 6"):
            print(f"  run {r['job']:8s} {r['key']}  {'ok' if r['ok'] else r['ok']}  {(r['note'] or '')[:80]}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="tape", description="Positioning Tape")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(fn=cmd_migrate)
    u = sub.add_parser("universe", help="resolve the AI complex against live meta")
    u.add_argument("--json", action="store_true")
    u.add_argument("--all", action="store_true", help="list every unclassified market")
    u.set_defaults(fn=cmd_universe)
    sub.add_parser("index", help="run the indexer (long-running)").set_defaults(fn=cmd_index)
    m = sub.add_parser("metrics", help="compute every finished sweep hour not yet computed")
    m.add_argument("--hour", action="append", help="recompute this hour (2026-10-31T19Z); repeatable")
    m.add_argument("--no-render", action="store_true")
    m.set_defaults(fn=cmd_metrics)
    r = sub.add_parser("render", help="render the site's charts for an hour (default: latest)")
    r.add_argument("--hour")
    r.set_defaults(fn=cmd_render)
    d = sub.add_parser("daily", help="render the daily chart and send it to Telegram")
    d.add_argument("--day", help="ET date, default today")
    d.add_argument("--dry-run", action="store_true", help="render and store, do not send")
    d.set_defaults(fn=cmd_daily)
    sub.add_parser("alerts", help="send pending event alerts").set_defaults(fn=cmd_alerts)
    e = sub.add_parser("event-posted", help="record the X post made for an event")
    e.add_argument("id", type=int)
    e.add_argument("url")
    e.set_defaults(fn=cmd_event_posted)
    mt = sub.add_parser("match", help="markets (and their latest chart) a post's text is about")
    mt.add_argument("text", nargs="+")
    mt.set_defaults(fn=cmd_match)
    sd = sub.add_parser("seed-leaderboard", help="queue leaderboard addresses for a one-time snapshot")
    sd.add_argument("--min-account-value", type=float, default=25_000)
    sd.set_defaults(fn=cmd_seed)
    sub.add_parser("status", help="sweeps, queues, coverage").set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
