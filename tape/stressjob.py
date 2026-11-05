"""The weekly stress test and its validation, against Postgres. The model is
tape/stress.py; this module loads its inputs, stores its outputs, renders the
cards and tells the operator.

  weekly   Sunday, after the 15:00 ET sweep and before Asia opens: replay
           recent drawdowns, then run every scenario on that sweep's
           positions and that hour's books. The operator gets the card in
           Telegram either way.
  replay   take the sweep before each recorded drawdown, apply the realized
           move, and compare the predicted forced selling with the
           liquidations the tape confirmed from userFillsByTime.
  release  publication is gated: complex coverage at the bar, the model
           validated on recorded episodes, and a person's wording review.
           `tape stress-release <date>` is that person's act; it refuses
           while either automatic gate is open. The site shows released
           runs only.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import loop, stress
from .charts import footer
from .db import connect
from .depth import ASK, BID, profile, size_within
from .graph import Graph, load_graph_file
from .render import ChartSize, HBar, render_bars, render_rounds
from .settings import Settings
from .standing import load_closes
from .store import ChartStore
from .telegram import Telegram
from .timeutil import ET, et_hour_on, hour_key, utcnow

log = logging.getLogger("tape.stress")

STRESS_LOCK = 0x7A9E_0003


# ---- inputs ----------------------------------------------------------------


def load_refs(conn, hour: datetime) -> dict[str, float]:
    rows = conn.execute(
        "select h.coin, h.mark from market_hours h join markets m on m.coin = h.coin and m.active where h.hour = %s",
        (hour,),
    ).fetchall()
    return {r["coin"]: r["mark"] for r in rows}


def load_accounts(conn, hour: datetime) -> list[stress.Account]:
    rows = conn.execute(
        """select s.address, m.dex, s.coin, s.size, s.position_value, s.margin_type, s.liq_px, s.margin_used,
                  m.max_leverage, a.cross_value, a.cross_maintenance, h.mark
           from snapshots s
           join markets m on m.coin = s.coin
           left join market_hours h on h.coin = s.coin and h.hour = s.hour
           left join accounts a on a.hour = s.hour and a.address = s.address and a.dex = m.dex
           where s.hour = %s""",
        (hour,),
    ).fetchall()
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by[(r["address"], r["dex"])].append(r)
    out = []
    for (addr, dex), rs in by.items():
        ps = []
        for r in rs:
            px0 = abs(r["position_value"] / r["size"]) if r["position_value"] and r["size"] else r["mark"]
            if not px0:
                continue
            ps.append(
                stress.Pos(
                    coin=r["coin"],
                    size=r["size"],
                    px0=px0,
                    margin_type=r["margin_type"],
                    liq_px=r["liq_px"],
                    margin_used=r["margin_used"],
                    mm_rate=stress.mm_rate(r["max_leverage"]),
                )
            )
        out.append(stress.Account(addr, dex, tuple(ps), rs[0]["cross_value"], rs[0]["cross_maintenance"]))
    return out


def load_books(
    conn, hour: datetime, max_age_h: int = 24
) -> tuple[dict[str, stress.Book], dict[str, float], datetime | None]:
    """Each market's book at `hour`, else its latest within `max_age_h` before.
    Returns the books, each book's 5% depth (USD, both sides) and the hour most
    books came from."""
    rows = conn.execute(
        """select distinct on (coin) coin, hour, mid, views from books
           where hour <= %s and hour > %s order by coin, hour desc""",
        (hour, hour - timedelta(hours=max_age_h)),
    ).fetchall()
    books, depth5, hours = {}, {}, defaultdict(int)
    for r in rows:
        views = r["views"] if isinstance(r["views"], dict) else json.loads(r["views"])
        ordered = [views[k] for k in sorted(views, key=_view_order) if views.get(k)]
        bid = profile(ordered, r["mid"], BID)
        ask = profile(ordered, r["mid"], ASK)
        books[r["coin"]] = stress.Book(bid, ask)
        depth5[r["coin"]] = (size_within(bid, 0.05) + size_within(ask, 0.05)) * r["mid"]
        hours[r["hour"]] += 1
    return books, depth5, (max(hours, key=hours.get) if hours else None)


def _view_order(k: str) -> int:
    return -1 if k == "full" else -int(k)  # full precision first, then 3, then 2


def depth_scales(conn, s: Settings, hour: datetime, depth5: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """(thick, central, thin) multipliers on each stored book: the window's
    same-hour 5% depth at the configured quantiles over the book's own."""
    days = int(s.t("stress", "depth_window_days"))
    qs = [float(q) for q in s.t("stress", "depth_quantiles")]
    rows = conn.execute(
        """select coin,
                  percentile_cont(%s) within group (order by bid5 + ask5) as q0,
                  percentile_cont(%s) within group (order by bid5 + ask5) as q1,
                  percentile_cont(%s) within group (order by bid5 + ask5) as q2
           from depth where time > %s and time <= %s
             and extract(hour from time at time zone 'UTC') = %s
           group by coin""",
        (qs[0], qs[1], qs[2], hour - timedelta(days=days), hour + timedelta(hours=1), hour.hour),
    ).fetchall()
    out = {}
    for r in rows:
        own = depth5.get(r["coin"])
        if own and own > 0 and r["q1"]:
            out[r["coin"]] = (r["q0"] / own, r["q1"] / own, r["q2"] / own)
    return out


def scenarios(
    s: Settings, g: Graph | None, refs: dict[str, float], closes: loop.Closes, day: date
) -> list[stress.Shock]:
    shocks = [float(x) for x in s.t("stress", "shocks")]
    kinds = set(s.t("stress", "node_kinds"))
    out: list[stress.Shock] = []
    if g is None:  # no graph: shock each market of the matching legs on its own
        legs = {"lab": ["labs"], "neocloud": ["neoclouds"], "memory": ["memory_names", "memory_price"]}
        coins = sorted({m for k in kinds for leg in legs.get(k, []) for m in s.universe.legs[leg].markets} & set(refs))
        return [stress.node_shock(c, c, [c], x) for c in coins for x in shocks]
    for n in g.nodes.values():
        ms = [m for m in n.markets if m in refs]
        if n.kind in kinds and ms:
            out += [stress.node_shock(n.key, n.name, ms, x) for x in shocks]
    days, min_days = int(s.t("stress", "beta_days")), int(s.t("stress", "min_beta_days"))
    for n in g.nodes.values():
        ms = [m for m in n.markets if m in refs]
        if n.kind not in set(s.t("stress", "graph_shock_kinds")) or not ms:
            continue
        deps = [m for k in g.dependents(n.key) for m in g.nodes[k].markets if m in refs and m not in ms]
        linked = loop.sell_off_betas(closes, g, n.key, deps, day, days, min_days)
        if linked:
            out += [stress.graph_shock(n.key, n.name, ms, x, linked) for x in shocks]
    return out


def complex_coverage(conn, hour: datetime) -> float | None:
    r = conn.execute(
        "select value from series where subject = 'complex' and metric = 'coverage' and hour = %s", (hour,)
    ).fetchone()
    return r["value"] if r else None


# ---- validation ------------------------------------------------------------


def replay(conn, s: Settings, lookback_days: int | None = None) -> list[dict]:
    """Replay every recorded drawdown in the window that starts at a finished
    sweep. Upserts stress_replays; returns the rows."""
    v = s.t("stress", "validation")
    lookback = int(lookback_days or v["lookback_days"])
    since = utcnow() - timedelta(days=lookback)
    swept = {
        r["hour"] for r in conn.execute("select hour from sweeps where finished_at is not null and hour > %s", (since,))
    }
    marks: dict[str, list[tuple]] = defaultdict(list)
    for r in conn.execute(
        """select h.coin, h.hour, h.mark from market_hours h join markets m on m.coin = h.coin
           where h.hour > %s order by h.hour""",
        (since,),
    ):
        marks[r["coin"]].append((r["hour"], r["mark"]))
    episodes = [e for e in stress.find_drawdowns(marks, float(v["min_move"]), int(v["window_h"])) if e[1] in swept]
    out = []
    for coin, start, trough, move in episodes:
        refs = load_refs(conn, start)
        if coin not in refs:
            continue
        accounts = load_accounts(conn, start)
        at_trough = load_refs(conn, trough)
        low = conn.execute(
            "select min(px) as p from fills where coin = %s and time > %s and time <= %s",
            (coin, start, trough + timedelta(hours=1)),
        ).fetchone()["p"]
        moves = {c: at_trough[c] / r - 1 for c, r in refs.items() if c in at_trough and r}
        if low:
            moves[coin] = min(moves.get(coin, 0.0), low / refs[coin] - 1)
        shock = stress.Shock(f"replay:{coin}@{hour_key(start)}", f"{coin} replay", "replay", moves, (coin,), move)
        res = stress.run(accounts, refs, {}, shock, walk_books=False)
        held = {(a.address, p.coin) for a in accounts for p in a.positions}
        recorded = sum(
            r["notional"]
            for r in conn.execute(
                "select address, coin, notional from liquidations where time > %s and time <= %s",
                (start, trough + timedelta(hours=1)),
            )
            if (r["address"], r["coin"]) in held
        )
        row = {
            "start_hour": start,
            "end_hour": trough,
            "coin": coin,
            "move": move,
            "predicted_usd": res.forced_usd,
            "recorded_usd": recorded,
            "ratio": res.forced_usd / recorded if recorded else None,
            "within": stress.within(res.forced_usd, recorded, float(v["tolerance"])),
            "detail": {"accounts_hit": res.accounts_hit, "positions_hit": res.positions_hit, "moves": moves},
        }
        conn.execute(
            """insert into stress_replays (start_hour, end_hour, coin, move, predicted_usd, recorded_usd, ratio, within, detail)
               values (%(start_hour)s, %(end_hour)s, %(coin)s, %(move)s, %(predicted_usd)s, %(recorded_usd)s,
                       %(ratio)s, %(within)s, %(detail)s)
               on conflict (start_hour, coin) do update set end_hour = excluded.end_hour, move = excluded.move,
                 predicted_usd = excluded.predicted_usd, recorded_usd = excluded.recorded_usd, ratio = excluded.ratio,
                 within = excluded.within, detail = excluded.detail, created_at = now()""",
            {**row, "detail": json.dumps(row["detail"], default=str)},
        )
        out.append(row)
    conn.commit()
    log.info("replayed %d episodes", len(out))
    return out


def validation(conn, s: Settings) -> dict:
    """The model gate: of the recent episodes with enough recorded liquidations,
    enough land within tolerance."""
    v = s.t("stress", "validation")
    rows = conn.execute(
        """select within from stress_replays where recorded_usd >= %s and start_hour > %s
           order by start_hour desc""",
        (float(v["min_recorded_usd"]), utcnow() - timedelta(days=int(v["lookback_days"]))),
    ).fetchall()
    n = len(rows)
    ok = sum(1 for r in rows if r["within"])
    share = ok / n if n else None
    passed = n >= int(v["min_episodes"]) and share is not None and share >= float(v["pass_share"])
    return {
        "model_validated": passed,
        "episodes": n,
        "within": ok,
        "why": (
            f"{ok} of {n} episodes within {float(v['tolerance']) * 100:.0f}%"
            if n
            else f"no episode yet with ${float(v['min_recorded_usd']):,.0f}+ recorded"
        )
        + ("" if passed else f" (needs {v['min_episodes']}+ episodes, {float(v['pass_share']) * 100:.0f}% within)"),
    }


# ---- the weekly run --------------------------------------------------------


def stress_day(s: Settings, now: datetime | None = None) -> date:
    """The latest stress date at or before now: the configured ET weekday."""
    d = (now or utcnow()).astimezone(ET).date()
    return d - timedelta(days=(d.weekday() - int(s.t("stress", "weekday_et"))) % 7)


def run_week(s: Settings, day: date | None = None, send: bool = True) -> dict:
    day = day or stress_day(s)
    hour = et_hour_on(day, int(s.t("stress", "sweep_hour_et")))
    with connect(s.env.database_url) as conn:
        got = conn.execute("select pg_try_advisory_lock(%s) as ok", (STRESS_LOCK,)).fetchone()["ok"]
        conn.commit()
        if not got:
            return {"skipped": "another stress run is in progress"}
        try:
            return _run_week(conn, s, day, hour, send)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (STRESS_LOCK,))
            conn.commit()


def _run_week(conn, s: Settings, day: date, hour: datetime, send: bool) -> dict:
    tg = Telegram(s.env)
    sweep = conn.execute("select finished_at from sweeps where hour = %s", (hour,)).fetchone()
    if not sweep or not sweep["finished_at"]:
        text = f"Stress {day}: the {hour.astimezone(ET):%H:%M} ET sweep is missing or unfinished; no run."
        if send:
            tg.message(text)
        return {"ran": False, "reason": "no sweep"}
    replay(conn, s)
    gates = validation(conn, s)
    cov = complex_coverage(conn, hour)
    floor = float(s.t("coverage", "post_min"))
    gates["coverage_ok"] = cov is not None and cov >= floor
    gates["coverage"] = cov

    refs = load_refs(conn, hour)
    accounts = load_accounts(conn, hour)
    books, depth5, book_hour = load_books(conn, hour)
    scales = depth_scales(conn, s, book_hour or hour, depth5)
    try:
        g = load_graph_file()
    except Exception:
        log.exception("graph load failed; shocking markets one at a time")
        g = None
    closes = load_closes(conn, since=day - timedelta(days=int(s.t("stress", "beta_days")) + 5))
    shocks = scenarios(s, g, refs, closes, day)
    max_rounds = int(s.t("stress", "max_rounds"))
    results = []
    for sh in shocks:
        thick, mid, thin = stress.run_range(accounts, refs, books, scales, sh, max_rounds)
        results.append((sh, thick, mid, thin))

    with conn.cursor() as cur:
        cur.execute("delete from stress_runs where date = %s", (day,))
        cur.execute(
            """insert into stress_runs (date, hour, book_hour, coverage, accounts, positions, gates)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            (
                day,
                hour,
                book_hour,
                cov,
                len(accounts),
                sum(len(a.positions) for a in accounts),
                json.dumps(gates, default=str),
            ),
        )
        cur.executemany(
            """insert into stress_results (date, scenario, label, kind, shock, forced_usd, forced_lo, forced_hi,
                 accounts_hit, markets_hit, amplification, rounds, unabsorbed_usd, detail)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    day,
                    sh.key,
                    sh.label,
                    sh.kind,
                    sh.shock,
                    mid.forced_usd,
                    min(r.forced_usd for r in (thick, mid, thin)),
                    max(r.forced_usd for r in (thick, mid, thin)),
                    mid.accounts_hit,
                    mid.markets_hit,
                    mid.amplification,
                    mid.rounds,
                    mid.unabsorbed_usd,
                    json.dumps({"markets": mid.detail(), "moves": sh.moves, "depth_scaled": sorted(scales)}),
                )
                for sh, thick, mid, thin in results
            ],
        )
    headline = float(s.t("stress", "headline_shock"))
    top = sorted((r for r in results if abs(r[0].shock - headline) < 1e-9), key=lambda r: r[2].forced_usd, reverse=True)
    store = ChartStore(s.env)
    size = (int(s.t("render", "width_px")), int(s.t("render", "height_px")))
    note = _caveat(cov, hour, book_hour)
    png = None
    if top:
        png = render_bars(
            title=f"Forced selling on Hyperliquid if one name falls {abs(headline) * 100:.0f}%",
            bars=[
                HBar(
                    sh.label.rsplit(" ", 1)[0],
                    mid.forced_usd,
                    min(r.forced_usd for r in (thick, mid, thin)),
                    max(r.forced_usd for r in (thick, mid, thin)),
                    f"{mid.accounts_hit} accts",
                )
                for sh, thick, mid, thin in top[:12]
            ],
            footer=footer(s, hour, note),
            xlabel="forced selling, USD (whisker: thick to thin book)",
            size=ChartSize(*size),
        )
        store.put(conn, "stress", "all", hour, png, size)
        sh, _, mid, _ = top[0]
        hit = sorted(
            (m for m in mid.markets.values() if sum(m.forced_usd) > 0 or m.coin in sh.shocked),
            key=lambda m: sum(m.forced_usd),
            reverse=True,
        )[:6]
        store.put(
            conn,
            "stress",
            "top",
            hour,
            render_rounds(
                title=f"{sh.label}: the cascade, round by round",
                paths={m.coin: [m.initial / m.ref - 1] + [p / m.ref - 1 for p in m.prices] for m in hit},
                forced={m.coin: m.forced_usd for m in hit},
                footer=footer(s, hour, note),
                size=ChartSize(*size),
            ),
            size,
        )
    conn.commit()
    cap = caption(s, day, hour, book_hour, top, gates)
    if send:
        if png:
            tg.photo(png, cap)
        else:
            tg.message(cap)
    conn.execute(
        """insert into runs (job, key, started_at, finished_at, ok, note) values ('stress', %s, now(), now(), true, %s)
           on conflict (job, key) do update set finished_at = now(), ok = true, note = excluded.note""",
        (day.isoformat(), cap[:500]),
    )
    conn.commit()
    return {"ran": True, "date": day.isoformat(), "scenarios": len(results), "gates": gates, "caption": cap}


def _caveat(cov: float | None, hour: datetime, book_hour: datetime | None) -> str:
    c = "n/a" if cov is None else f"{cov * 100:.1f}%"
    b = "no books" if book_hour is None else f"books {book_hour.astimezone(ET):%a %H:%M} ET"
    return f"Hyperliquid only, one venue. Coverage {c}. Positions {hour.astimezone(ET):%a %H:%M} ET, {b}."


def money(x: float) -> str:
    a = abs(x)
    if a >= 1e9:
        return f"${x / 1e9:,.2f}B"
    if a >= 1e6:
        return f"${x / 1e6:,.1f}M"
    return f"${x / 1e3:,.0f}k"


def headline(sh: stress.Shock, mid: stress.Result) -> str:
    amp = "n/a" if mid.amplification is None else f"{mid.amplification:.2f}×"
    return (
        f"A {abs(sh.shock) * 100:.0f}% drop in {sh.label.rsplit(' ', 1)[0]} forces {money(mid.forced_usd)} "
        f"of selling across {mid.markets_hit} markets on Hyperliquid; amplification {amp}."
    )


def caption(s: Settings, day: date, hour: datetime, book_hour, top, gates: dict) -> str:
    lines = [f"Stress test {day}  ·  {_caveat(gates.get('coverage'), hour, book_hour)}"]
    hits = [r for r in top if r[2].forced_usd > 0]
    if not hits:
        lines.append(f"No scenario at {abs(float(s.t('stress', 'headline_shock'))) * 100:.0f}% forces any selling.")
    for sh, thick, mid, thin in hits[:3]:
        lo = min(r.forced_usd for r in (thick, mid, thin))
        hi = max(r.forced_usd for r in (thick, mid, thin))
        lines.append(headline(sh, mid) + f" Range {money(lo)} to {money(hi)}; {mid.accounts_hit} accounts.")
    ok = gates.get("coverage_ok") and gates.get("model_validated")
    lines.append(
        f"Gates: coverage {'ok' if gates.get('coverage_ok') else 'under the bar'}; model "
        f"{'validated' if gates.get('model_validated') else 'not validated'} ({gates.get('why')})."
    )
    if ok:
        lines.append(f"After the wording review: tape stress-release {day} publishes {s.env.site_url}/stress/{day}")
    else:
        lines.append("NOT FOR POSTING: the site will not show this run.")
    return "\n".join(lines)


def release(s: Settings, day: date) -> str:
    with connect(s.env.database_url) as conn:
        r = conn.execute("select hour, gates, released_at from stress_runs where date = %s", (day,)).fetchone()
        if not r:
            raise SystemExit(f"no stress run for {day}")
        if r["released_at"]:
            return f"already released at {r['released_at']}"
        gates = validation(conn, s)
        cov = (r["gates"] or {}).get("coverage")
        floor = float(s.t("coverage", "post_min"))
        if cov is None or cov < floor:
            raise SystemExit(f"not released: complex coverage {cov} is under {floor}")
        if not gates["model_validated"]:
            raise SystemExit(f"not released: model not validated ({gates['why']})")
        conn.execute(
            "update stress_runs set released_at = now(), gates = gates || %s where date = %s",
            (json.dumps({**gates, "released_by": "tape stress-release"}), day),
        )
        conn.commit()
    return f"released {s.env.site_url}/stress/{day}"


def weekly_job(s: Settings, force: bool = False) -> dict:
    """The Vercel cron entry: runs on the stress weekday from an hour after
    the sweep hour onward, once per date."""
    now_et = utcnow().astimezone(ET)
    want_h = int(s.t("stress", "sweep_hour_et")) + 1
    if not force and (now_et.weekday() != int(s.t("stress", "weekday_et")) or now_et.hour != want_h):
        return {
            "skipped": f"it is {now_et:%a %H:%M} ET; the stress test runs {want_h}:xx ET on weekday {s.t('stress', 'weekday_et')}"
        }
    day = now_et.date()
    with connect(s.env.database_url) as conn:
        done = conn.execute(
            "select 1 from runs where job = 'stress' and key = %s and ok", (day.isoformat(),)
        ).fetchone()
    if done and not force:
        return {"skipped": f"stress for {day} already ran"}
    return run_week(s, day, send=True)
