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
        print(
            json.dumps(
                {
                    "markets": {c: m.leg for c, m in u.markets.items()},
                    "missing": u.missing,
                    "delisted": u.delisted,
                    "candidates": u.candidates,
                    "unclassified": u.unclassified,
                },
                indent=2,
            )
        )
        return
    for leg in s.universe.leg_order:
        print(f"{s.universe.legs[leg].label}:")
        for c in u.leg_markets(leg):
            x = ctx[c]
            oi_usd = float(x["openInterest"]) * float(x["markPx"])
            print(
                f"  {c:14s} max {u.markets[c].max_leverage:>2}x  OI ${oi_usd / 1e6:8.2f}M  "
                f"24h vol ${float(x['dayNtlVlm']) / 1e6:8.2f}M"
            )
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
    print(
        f"queued {len(addrs)} leaderboard addresses (account value >= ${a.min_account_value:,.0f}) "
        f"on {len(s.universe.dexes)} dexes; the indexer snapshots them at low priority"
    )


def cmd_status(a) -> None:
    from .db import connect

    s = _settings()
    with connect(s.env.database_url) as conn:
        q = lambda sql, *p: conn.execute(sql, p).fetchall()  # noqa: E731
        print("sweeps:")
        for r in q("select * from sweeps order by hour desc limit 5"):
            dur = (r["finished_at"] - r["started_at"]).total_seconds() if r["finished_at"] else None
            print(
                f"  {r['hour']:%Y-%m-%d %H:%M}Z  {r['addresses'] or 0:>6} swept  {r['positions'] or 0:>6} positions  "
                f"{r['errors'] or 0} errors  {f'{dur:.0f}s' if dur else 'running'}"
            )
        c = q("""select (select count(*) from addresses) a, (select count(*) from address_dexes where seed_pending) seeds,
                 (select count(*) from addresses where history_checked_at is null) hist,
                 (select count(*) from fills) fills, (select max(time) from fills) last_fill,
                 (select count(*) from events) events""")[0]
        print(
            f"addresses {c['a']}  seed queue {c['seeds']}  history queue {c['hist']}  fills {c['fills']}  "
            f"last fill {c['last_fill']}  events {c['events']}"
        )
        d = q("""select (select max(time) from depth) last_depth,
                   (select count(distinct coin) from depth where time > now() - interval '10 minutes') depth_markets,
                   (select max(hour) from accounts) last_accounts,
                   (select count(*) from accounts where hour = (select max(hour) from accounts)) accounts""")[0]
        print(
            f"depth: last {d['last_depth']}, {d['depth_markets']} markets in the last 10 min  ·  "
            f"account margin: {d['accounts']} accounts at {d['last_accounts']}"
        )
        h = q("select max(hour) h from series")[0]["h"]
        if h:
            print(f"coverage at {h:%Y-%m-%d %H:%M}Z:")
            for r in q(
                """select subject, value from series where hour = %s and metric = 'coverage'
                          order by subject = 'complex' desc, subject like 'leg:%%' desc, subject""",
                h,
            ):
                flag = "" if r["value"] >= float(s.t("coverage", "post_min")) else "  < post bar"
                print(f"  {r['subject']:22s} {r['value'] * 100:6.1f}%{flag}")
        for r in q("select job, key, ok, note from runs order by started_at desc limit 6"):
            print(f"  run {r['job']:8s} {r['key']}  {'ok' if r['ok'] else r['ok']}  {(r['note'] or '')[:80]}")


def cmd_stress(a) -> None:
    from datetime import date

    from . import stressjob

    out = stressjob.run_week(_settings(), date.fromisoformat(a.day) if a.day else None, send=not a.dry_run)
    print(json.dumps(out, indent=2, default=str))


def cmd_stress_replay(a) -> None:
    from . import stressjob
    from .db import connect

    s = _settings()
    with connect(s.env.database_url) as conn:
        rows = stressjob.replay(conn, s, a.days)
        gate = stressjob.validation(conn, s)
    for r in rows:
        ratio = "n/a" if r["ratio"] is None else f"{r['ratio']:.2f}"
        print(
            f"{r['start_hour']:%Y-%m-%d %H}Z {r['coin']:12s} {r['move'] * 100:+6.1f}%  predicted "
            f"${r['predicted_usd'] / 1e6:8.2f}M  recorded ${r['recorded_usd'] / 1e6:8.2f}M  ratio {ratio}  "
            f"{'within' if r['within'] else 'outside'}"
        )
    print("model validated:" if gate["model_validated"] else "model NOT validated:", gate["why"])


def cmd_stress_release(a) -> None:
    from datetime import date

    from .stressjob import release

    print(release(_settings(), date.fromisoformat(a.day)))


def cmd_graph(a) -> None:
    from datetime import date

    from .graph import load_graph_file

    s = _settings()
    g = load_graph_file()
    today = date.today()
    stale = int(s.t("graph", "stale_days"))
    print(f"{len(g.nodes)} nodes, {len(g.edges)} verified edges, {len(g.drafts)} drafts")
    unpriced = sorted(set(s.universe.markets_all()) - set(g.node_of_market()))
    if unpriced:
        print("markets in the universe with no node:", " ".join(unpriced))
    for e in g.edges:
        if e.stale(today, stale):
            print(f"  stale  {e.id:28s} verified {e.verified_at}")
    if a.drafts:
        for e in g.drafts:
            size = "" if e.size_usd is None else f" ${e.size_usd / 1e9:,.1f}B"
            print(f"  draft  {e.id:28s} {e.src} -> {e.dst} {e.type}{size} ({e.confidence}) {e.source}")
    for k, x in sorted(g.exposures().items()):
        if x:
            print(f"  {k:16s} {x}")


def cmd_candles(a) -> None:
    from .db import connect
    from .hl import InfoClient, WeightBucket
    from .timeutil import from_ms, ms, utcnow

    s = _settings()
    coins = sorted(s.universe.markets_all())
    end = utcnow()

    async def go():
        ix = s.t("indexer")
        info = InfoClient(
            s.env.info_url, WeightBucket(float(ix["info_weight_per_minute"]) * float(ix["budget_fraction"]))
        )
        out = {}
        try:
            for c in coins:
                try:
                    out[c] = await info.candles(c, "1d", ms(end) - a.days * 86_400_000, ms(end))
                except Exception as e:
                    print(f"  {c}: {e}")
        finally:
            await info.close()
        return out

    got = asyncio.run(go())
    today = end.date()
    rows = [
        (c, from_ms(int(k["t"])).date(), float(k["c"]))
        for c, ks in got.items()
        for k in ks
        if from_ms(int(k["t"])).date() < today  # today's candle is not closed
    ]
    with connect(s.env.database_url) as conn, conn.cursor() as cur:
        cur.executemany(
            """insert into candles (coin, day, close, source) values (%s, %s, %s, 'candle')
               on conflict (coin, day) do update set close = excluded.close, source = 'candle'""",
            rows,
        )
        conn.commit()
    print(f"stored {len(rows)} daily closes for {len(got)} markets")


def cmd_stack(a) -> None:
    from .modeljob import check

    print("\n".join(check(a.drafts)))


def cmd_model(a) -> None:
    from datetime import date

    from . import modeljob

    out = modeljob.run(_settings(), date.fromisoformat(a.day) if a.day else None, drafts=a.drafts, store=not a.dry_run)
    print(json.dumps(out, indent=2, default=str))


LOADERS = ("edgar", "taiwan", "korea", "rents")


def cmd_load(a) -> None:
    from .loaders import edgar, korea, sfcompute, taiwan

    mods = {"edgar": edgar, "taiwan": taiwan, "korea": korea, "rents": sfcompute}
    s = _settings()
    for name in a.which or ["edgar", "taiwan", "korea"]:
        try:
            out = mods[name].load(s)
        except Exception as e:  # one source down must not stop the others
            out = {"error": f"{type(e).__name__}: {e}"}
        print(name, json.dumps(out, default=str))


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
    st = sub.add_parser("stress", help="the stress test on real positions (monthly exhibit)")
    st.add_argument("--day", help="ET date of the run (default: the latest stress weekday)")
    st.add_argument("--dry-run", action="store_true", help="compute and store, do not send")
    st.set_defaults(fn=cmd_stress)
    sr = sub.add_parser("stress-replay", help="replay recorded drawdowns against recorded liquidations")
    sr.add_argument("--days", type=int, help="window, default stress.validation.lookback_days")
    sr.set_defaults(fn=cmd_stress_replay)
    rl = sub.add_parser("stress-release", help="publish a stress run on the site, after the wording review")
    rl.add_argument("day")
    rl.set_defaults(fn=cmd_stress_release)
    gr = sub.add_parser("graph", help="check config/graph.yaml: nodes, verified edges, stale edges, drafts")
    gr.add_argument("--drafts", action="store_true", help="list the draft edges awaiting verification")
    gr.set_defaults(fn=cmd_graph)
    cd = sub.add_parser("candles", help="backfill daily closes for the standing series")
    cd.add_argument("--days", type=int, default=400)
    cd.set_defaults(fn=cmd_candles)
    sk = sub.add_parser("stack", help="check config/stack: what loads, what is a draft")
    sk.add_argument("--drafts", action="store_true", help="list draft figures, judgments and comparisons")
    sk.set_defaults(fn=cmd_stack)
    md = sub.add_parser("model", help="the daily model run: estimates, gaps, scenarios, forecast scores")
    md.add_argument("--day", help="UTC date, default today")
    md.add_argument("--drafts", action="store_true", help="treat drafts as verified; stores nothing")
    md.add_argument("--dry-run", action="store_true", help="compute without storing")
    md.set_defaults(fn=cmd_model)
    ld = sub.add_parser("load", help="run loaders: edgar, taiwan, korea (default: all three) or rents")
    ld.add_argument("which", nargs="*", choices=LOADERS)
    ld.set_defaults(fn=cmd_load)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
