"""The tape's jobs as bounded functions, for Vercel (see tape/web.py and vercel.json).

A box runs `tape index` forever. Vercel functions stop after maxDuration, so
the same work is cut into pieces that each finish in time:

  shift   every 10 min, lasting ~11.5: the WebSocket and every background loop.
          Consecutive shifts overlap, so the trade feed always has a listener;
          fills are keyed on (coin, tid), so the overlap writes nothing twice.
  sweep   every 15 min: continue this hour's clearinghouseState sweep where
          the last invocation stopped, then run metrics and charts once the
          hour is finished.
  daily   16:00 ET (two UTC schedules; only the one that lands on 16:00 ET runs).
  stress  Sunday 16:30 ET, after the 15:00 ET sweep and before Asia opens
          (two UTC schedules; only the one that lands on 16:xx ET runs, and
          with the monthly cadence only on the month's first Sunday).
  model   daily 01:30 UTC, after the 00:00 UTC closes: the stack model run.
  load    daily 06:00 UTC: EDGAR, Taiwan, Korea and Ornn loaders (each writes
          only what is new, so release days need no calendar).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
from datetime import date

from . import cli, daily, metrics
from .db import aconnect, connect, migrate
from .indexer import Indexer
from .settings import Settings
from .timeutil import ET, floor_hour, utcnow

log = logging.getLogger("tape.worker")

# Commands the task endpoint may run. `index` is the long-running process and
# has no place in a function.
TASKS = {
    "migrate",
    "universe",
    "metrics",
    "render",
    "daily",
    "alerts",
    "event-posted",
    "match",
    "seed-leaderboard",
    "status",
    "stress",
    "stress-replay",
    "stress-release",
    "graph",
    "candles",
    "stack",
    "model",
    "load",
    "gaps",
}


def _budget(s: Settings) -> float:
    ix = s.t("indexer")
    return float(ix["info_weight_per_minute"]) * float(ix["budget_fraction"])


async def shift(s: Settings) -> dict:
    v = s.t("vercel")
    conn = await aconnect(s.env.database_url)
    wconn = await aconnect(s.env.database_url)
    ix = Indexer(s, conn, wconn)
    try:
        full = _budget(s)
        return await ix.run_shift(
            seconds=float(v["shift_s"]),
            overlap=float(v["shift_overlap_s"]),
            budget=full,
            sweep_budget=full * float(v["sweep_budget_share"]),
        )
    finally:
        await ix.info.close()
        with contextlib.suppress(Exception):
            await conn.close()
        with contextlib.suppress(Exception):
            await wconn.close()


async def sweep(s: Settings, started: float | None = None) -> dict:
    """Advance this hour's sweep; compute metrics when it is finished."""
    started = started or time.monotonic()
    v = s.t("vercel")
    await asyncio.to_thread(migrate, s.env.database_url)
    offset = float(s.t("indexer", "sweep_offset_s"))
    now = utcnow()
    hour = floor_hour(now)
    wait = offset - (now - hour).total_seconds()
    if wait > 0:
        await asyncio.sleep(wait)
    conn = await aconnect(s.env.database_url)
    ix = Indexer(s, conn, conn)
    ix.bucket.set_rate(_budget(s) * float(v["sweep_budget_share"]))
    out: dict = {"hour": hour.isoformat()}
    try:
        await ix.load_universe()
        done = await ix.sweep_step(hour, deadline=started + float(v["sweep_stop_s"]))
        out["sweep"] = {True: "finished", False: "stopped for time", None: "locked"}[done]
    finally:
        await ix.info.close()
        with contextlib.suppress(Exception):
            await conn.close()
    left = 800 - (time.monotonic() - started)
    if left >= float(v["metrics_min_s"]):
        with connect(s.env.database_url) as c:
            hours = await asyncio.to_thread(metrics.run, c, s)
        out["metrics"] = [h.isoformat() for h in hours]
    else:
        out["metrics"] = f"deferred ({left:.0f}s left)"
    log.info("sweep job: %s", out)
    return out


def daily_job(s: Settings, force: bool = False) -> dict:
    now_et = utcnow().astimezone(ET)
    if not force and now_et.hour != 16:
        return {"skipped": f"it is {now_et:%H:%M} ET, not 16:00"}
    day: date = now_et.date()
    with connect(s.env.database_url) as conn:
        sent = conn.execute(
            "select 1 from runs where job = 'daily' and key = %s and finished_at is not null", (day.isoformat(),)
        ).fetchone()
    if sent and not force:
        return {"skipped": f"daily for {day} already ran"}
    return daily.run(s, day, send=True)


def load_job(s: Settings) -> dict:
    from .loaders import edgar, korea, ornn, taiwan

    out = {}
    for name, mod in (("edgar", edgar), ("taiwan", taiwan), ("korea", korea), ("ornn", ornn)):
        try:
            out[name] = mod.load(s)
        except Exception as e:  # one source down must not stop the others
            log.exception("loader %s failed", name)
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def task(args: list[str]) -> dict:
    """Run one CLI command and return what it printed."""
    if not args or args[0] not in TASKS:
        return {"error": f"task must be one of {sorted(TASKS)}"}
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            cli.main(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        log.exception("task %s failed", args)
        buf.write(f"\n{type(e).__name__}: {e}\n")
        code = 1
    finally:
        root.removeHandler(handler)
    return {"args": args, "exit": code, "output": buf.getvalue()}
