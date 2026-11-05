"""The indexer: one asyncio process holding the WebSocket and the hourly sweep.

Loops, all in one process:
  ws         subscribe to `trades` for every market in the universe; every fill
             names both counterparties, which is how addresses are discovered
  writer     persist fills and the address set in batches
  sweep      at the top of each hour, clearinghouseState for every address in
             the sweep set, written to `snapshots`; then the metrics job runs
  discover   snapshot newly seen addresses right away so their running
             position (and event alerts) is known before the next sweep
  history    estimate when each address first traded the complex (fresh cohort)
  seed       snapshot leaderboard addresses once (`tape seed-leaderboard`)
  liq        confirm liquidation candidates from userFillsByTime
  alerts     send pending event alerts to Telegram
  refresh    re-read the universe and the whale set on a schedule
  depth      subscribe to `l2Book` for every market at each aggregation in
             `depth.sig_figs`; banded depth every minute, a full book every hour

On a box, `tape index` runs all of them forever (`run`). On Vercel, where a
function stops after maxDuration, the same loops run as overlapping shifts
(`run_shift`) and the sweep runs as its own resumable function (`sweep_step`);
see tape/worker.py.

Positions are never reconstructed from fills: fills only move the running
position between snapshots so real-time alerts fire; the next sweep replaces
the running position with the exact one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

import websockets

from . import alerts
from .depth import bands_usd, parse_ws_book
from .events import EventRules, position_events
from .fills import Fill, estimate_first_fill, from_user_fill, from_ws_trade, liquidated_user
from .hl import HIGH, LOW, InfoClient, WeightBucket
from .settings import Settings
from .store import load_universe, write_markets
from .timeutil import et_day, floor_hour, from_ms, ms, utcnow
from .universe import Universe

log = logging.getLogger("tape.indexer")

WS_PING_S = 30
SWEEP_CONCURRENCY = 8
BACKGROUND_CONCURRENCY = 4
HISTORY_PAGES = 3
SWEEP_CHUNK = 200
SWEEP_LOCK = 0x7A9E_0002  # advisory lock id: one sweeper at a time


def _clock() -> float:
    """time.monotonic, behind a name tests can replace."""
    return time.monotonic()


class Indexer:
    def __init__(self, settings: Settings, conn, wconn):
        self.s = settings
        ix = settings.t("indexer")
        per_min = float(ix["info_weight_per_minute"]) * float(ix["budget_fraction"])
        self.bucket = WeightBucket(per_min, float(ix["low_priority_reserve"]))
        self.info = InfoClient(settings.env.info_url, self.bucket)
        self.db = conn  # general queries, guarded by db_lock
        self.wdb = wconn  # fill writer only
        self.db_lock = asyncio.Lock()
        self.universe: Universe | None = None
        self.rules = EventRules.from_config(settings.t("events"))

        self.fill_q: asyncio.Queue[Fill] = asyncio.Queue(maxsize=200_000)
        self.discover_q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.liq_q: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        self.event_q: asyncio.Queue = asyncio.Queue()

        self.addr_dex_seen: set[tuple[str, str]] = set()
        self.addr_seen: set[str] = set()
        self.positions: dict[tuple[str, str], float] = defaultdict(float)
        self.liq_px: dict[tuple[str, str], float] = {}
        self.known: dict[str, set[str]] = defaultdict(set)  # dex -> addresses whose running position is exact
        self.whales: set[str] = set()
        self.marks: dict[str, float] = {}
        self.alerts_from: datetime | None = None

        self.books: dict[tuple[str, str], tuple[float, list, list]] = {}  # (view, coin) -> (t, bids, asks)
        self.books_written: set[tuple[str, datetime]] = set()

        self.last_msg_ms: int | None = None
        self.writing = False
        self.bg_tasks: list[asyncio.Task] = []
        self.resubscribe = asyncio.Event()
        self.stats = defaultdict(int)

    # ---- startup -------------------------------------------------------

    async def q(self, sql: str, params=None) -> list[dict]:
        async with self.db_lock:
            cur = await self.db.execute(sql, params)
            return await cur.fetchall() if cur.description else []

    async def load_universe(self) -> None:
        dexs = await self.info.perp_dexs()
        metas = {}
        live = {d["name"] for d in dexs if d}
        for dex in self.s.universe.dexes:
            if dex in live:
                metas[dex] = await self.info.meta_and_ctxs(dex)
            else:
                log.error("dex %s is not in perpDexs; its markets are dropped", dex)
        old = set(self.universe.coins) if self.universe else set()
        self.universe = load_universe(self.s.universe, metas)
        for resp in metas.values():
            for asset, ctx in zip(resp[0]["universe"], resp[1], strict=True):
                if asset["name"] in self.universe.markets and ctx.get("markPx"):
                    self.marks[asset["name"]] = float(ctx["markPx"])
        async with self.db_lock:
            await write_markets(self.db, self.s.universe, self.universe)
        u = self.universe
        log.info("universe: %d markets on %s; missing %s; delisted %s", len(u.markets), u.dexes, u.missing, u.delisted)
        if u.unclassified or u.candidates:
            log.info("live but not in the complex: candidates %s; unclassified %d", u.candidates, len(u.unclassified))
        if old and set(u.coins) != old:
            log.warning(
                "universe changed: +%s -%s; resubscribing", sorted(set(u.coins) - old), sorted(old - set(u.coins))
            )
            self.resubscribe.set()

    async def load_state(self) -> None:
        rows = await self.q("select address, dex from address_dexes")
        self.addr_dex_seen = {(r["address"], r["dex"]) for r in rows}
        self.addr_seen = {a for a, _ in self.addr_dex_seen}
        last = await self.q(
            "select hour, finished_at from sweeps where finished_at is not null order by hour desc limit 1"
        )
        first = await self.q("select min(finished_at) as t from sweeps where finished_at is not null")
        warm = timedelta(hours=float(self.s.t("events", "warmup_hours")))
        self.alerts_from = (first[0]["t"] + warm) if first and first[0]["t"] else None
        if last and utcnow() - last[0]["finished_at"] < timedelta(hours=2):
            hour = last[0]["hour"]
            snaps = await self.q("select address, coin, size, liq_px from snapshots where hour = %s", (hour,))
            for r in snaps:
                self.positions[(r["address"], r["coin"])] = r["size"]
                if r["liq_px"]:
                    self.liq_px[(r["address"], r["coin"])] = r["liq_px"]
            swept = await self.q("select address, dex from address_dexes where first_swept_hour <= %s", (hour,))
            for r in swept:
                self.known[r["dex"]].add(r["address"])
            # Fills since the sweep finished move the running positions on, so a
            # process that starts mid-hour (a Vercel shift) does not start an hour stale.
            moved = await self.q("select coin, buyer, seller, sz from fills where time > %s", (last[0]["finished_at"],))
            for f in moved:
                if f["buyer"]:
                    self.positions[(f["buyer"], f["coin"])] += f["sz"]
                if f["seller"]:
                    self.positions[(f["seller"], f["coin"])] -= f["sz"]
            log.info(
                "running positions from sweep %s: %d positions, %d known, %d fills replayed",
                hour,
                len(snaps),
                len(swept),
                len(moved),
            )
        await self.refresh_whales()
        mx = await self.q("select max(time) as t from fills where source = 'ws'")
        if mx and mx[0]["t"]:
            self.last_msg_ms = ms(mx[0]["t"])

    async def refresh_whales(self) -> None:
        rows = await self.q(
            "select address from address_days where whale_rank is not null and day = (select max(day) from address_days)"
        )
        self.whales = {r["address"] for r in rows}

    def alerts_on(self) -> bool:
        return self.alerts_from is not None and utcnow() >= self.alerts_from

    # ---- websocket -----------------------------------------------------

    async def ws_loop(self) -> None:
        backoff = 1.0
        while True:
            coins = self.universe.coins
            try:
                async with websockets.connect(self.s.env.ws_url, ping_interval=None, max_size=2**24) as ws:
                    for c in coins:
                        await ws.send(
                            json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}})
                        )
                    log.info("ws subscribed to %d markets", len(coins))
                    self.resubscribe.clear()
                    self.schedule_gap_backfill()
                    backoff = 1.0
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        while not self.resubscribe.is_set():
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            except TimeoutError:
                                continue
                            self.on_message(json.loads(raw))
                    finally:
                        pinger.cancel()
            except (OSError, websockets.WebSocketException) as e:
                log.warning("ws dropped (%s); reconnecting in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(WS_PING_S)
            await ws.send(json.dumps({"method": "ping"}))

    def schedule_gap_backfill(self) -> None:
        gap_s = float(self.s.t("indexer", "backfill_gap_s"))
        now = ms(utcnow())
        if self.last_msg_ms and now - self.last_msg_ms > gap_s * 1000:
            start = max(self.last_msg_ms, now - 24 * 3600 * 1000)
            log.warning("ws gap of %.0fs; backfilling from userFillsByTime", (now - self.last_msg_ms) / 1000)
            self.bg_tasks.append(asyncio.create_task(self.backfill(start, now)))

    def on_message(self, msg: dict) -> None:
        if msg.get("channel") != "trades":
            return
        for t in msg["data"]:
            f = from_ws_trade(t)
            if f.coin not in self.universe.markets:
                continue
            self.last_msg_ms = max(self.last_msg_ms or 0, f.time_ms)
            self.marks[f.coin] = f.px
            self.apply_fill(f)
            try:
                self.fill_q.put_nowait(f)
            except asyncio.QueueFull:
                self.stats["fills_dropped"] += 1

    def apply_fill(self, f: Fill) -> None:
        """Move running positions and look for real-time events."""
        dex = self.universe.markets[f.coin].dex
        for addr, delta in ((f.buyer, f.sz), (f.seller, -f.sz)):
            if not addr:
                continue
            key = (addr, f.coin)
            prev = self.positions.get(key, 0.0)
            new = prev + delta
            self.positions[key] = new
            if addr not in self.known[dex]:
                continue
            if self.alerts_on():
                for ev in position_events(
                    time=f.time,
                    et_day=et_day(f.time).isoformat(),
                    coin=f.coin,
                    address=addr,
                    prev_size=prev,
                    new_size=new,
                    mark=f.px,
                    is_whale=addr in self.whales,
                    rules=self.rules,
                ):
                    self.event_q.put_nowait(ev)
            liq = self.liq_px.get(key)
            if liq and abs(new) < abs(prev):
                band = float(self.s.t("indexer", "liq_px_band"))
                if (prev > 0 and f.px <= liq * (1 + band)) or (prev < 0 and f.px >= liq * (1 - band)):
                    self.liq_q.put_nowait((addr, f.coin, f.time_ms))

    # ---- depth ---------------------------------------------------------

    def depth_views(self) -> list[str]:
        return [str(v) for v in self.s.t("depth", "sig_figs")]

    async def depth_loop(self) -> None:
        """One WebSocket per aggregation: l2Book messages do not say which
        nSigFigs they were subscribed at, so each view gets its own connection."""
        views = self.depth_views()
        await asyncio.gather(*(self._book_feed(v) for v in views), self._depth_sampler(views))

    async def _book_feed(self, view: str) -> None:
        backoff = 1.0
        while True:
            coins = self.universe.coins
            try:
                async with websockets.connect(self.s.env.ws_url, ping_interval=None, max_size=2**24) as ws:
                    for c in coins:
                        sub: dict = {"type": "l2Book", "coin": c}
                        if view != "full":
                            sub["nSigFigs"] = int(view)
                        await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
                    log.info("depth ws (%s) subscribed to %d books", view, len(coins))
                    backoff = 1.0
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        while self.universe.coins == coins:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            except TimeoutError:
                                continue
                            msg = json.loads(raw)
                            if msg.get("channel") == "l2Book":
                                d = msg["data"]
                                self.books[(view, d["coin"])] = (time.monotonic(), *parse_ws_book(d))
                    finally:
                        pinger.cancel()
            except (OSError, websockets.WebSocketException) as e:
                log.warning("depth ws (%s) dropped (%s); reconnecting in %.0fs", view, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def depth_rows(self, views: list[str], now: datetime, max_age_s: float) -> tuple[list[tuple], list[tuple]]:
        """Minute rows for `depth` and, for markets without one this hour, a row for `books`."""
        minute = now.replace(second=0, microsecond=0)
        hour = floor_hour(now)
        fresh_after = time.monotonic() - max_age_s
        rows, book_rows = [], []
        for c in self.universe.coins:
            got = {v: self.books.get((v, c)) for v in views}
            got = {v: (b[1], b[2]) for v, b in got.items() if b and b[0] >= fresh_after}
            if views[0] not in got:
                continue
            b = bands_usd([got[v] for v in views if v in got])
            if not b:
                continue
            rows.append(
                (
                    c,
                    minute,
                    b["mid"],
                    b["bid2"],
                    b["bid5"],
                    b["bid10"],
                    b["ask2"],
                    b["ask5"],
                    b["ask10"],
                    b["reach_bid"],
                    b["reach_ask"],
                )
            )
            if (c, hour) not in self.books_written:
                book_rows.append((c, hour, now, b["mid"], json.dumps(got)))
        return rows, book_rows

    async def _depth_sampler(self, views: list[str]) -> None:
        every = float(self.s.t("depth", "sample_s"))
        while True:
            await asyncio.sleep(every - (time.time() % every) + 1)  # just after each minute
            rows, book_rows = self.depth_rows(views, utcnow(), 2 * every)
            if not rows:
                continue
            try:
                # Two overlapping shifts sample the same minute; the first write wins.
                async with self.db_lock, self.db.cursor() as cur:
                    await cur.executemany(
                        """insert into depth (coin, time, mid, bid2, bid5, bid10, ask2, ask5, ask10, reach_bid, reach_ask)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing""",
                        rows,
                    )
                    if book_rows:
                        await cur.executemany(
                            """insert into books (coin, hour, time, mid, views) values (%s, %s, %s, %s, %s)
                               on conflict do nothing""",
                            book_rows,
                        )
                self.books_written.update((r[0], r[1]) for r in book_rows)
                self.stats["depth_rows"] += len(rows)
            except Exception:
                log.exception("depth write failed")

    # ---- writer --------------------------------------------------------

    async def writer_loop(self) -> None:
        while True:
            self.writing = False
            batch = [await self.fill_q.get()]
            self.writing = True
            while len(batch) < 5000:
                try:
                    batch.append(self.fill_q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await self.write_fills(batch)
            except Exception:
                log.exception("fill write failed; %d fills requeued", len(batch))
                await asyncio.sleep(5)
                for f in batch:
                    self.fill_q.put_nowait(f)
            evs = []
            while not self.event_q.empty():
                evs.append(self.event_q.get_nowait())
            if evs:
                # The writer's own connection: a shift cancels the background loops
                # mid-query at its end, which can leave the shared connection unusable.
                await insert_events_async(self.wdb, evs)
            self.writing = False
            await asyncio.sleep(1)

    async def write_fills(self, batch: list[Fill]) -> None:
        last_by_addr_dex: dict[tuple[str, str], datetime] = {}
        for f in batch:
            dex = self.universe.markets[f.coin].dex if f.coin in self.universe.markets else f.coin.split(":")[0]
            for a in (f.buyer, f.seller):
                if a:
                    k = (a, dex)
                    last_by_addr_dex[k] = max(last_by_addr_dex.get(k, f.time), f.time)
        # Two overlapping shifts upsert the same address rows; a fixed order keeps
        # them from deadlocking.
        last_by_addr_dex = dict(sorted(last_by_addr_dex.items()))
        async with self.wdb.cursor() as cur:
            await cur.executemany(
                """insert into fills (coin, tid, time, px, sz, side, buyer, seller, hash, source)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (coin, tid) do update set
                     buyer = coalesce(fills.buyer, excluded.buyer),
                     seller = coalesce(fills.seller, excluded.seller),
                     side = coalesce(fills.side, excluded.side)""",
                [
                    (f.coin, f.tid, f.time, f.px, f.sz, f.side, f.buyer, f.seller, f.hash, f.source)
                    for f in sorted(batch, key=lambda f: (f.coin, f.tid))
                ],
            )
            by_addr: dict[str, datetime] = {}
            for (a, _), t in last_by_addr_dex.items():
                by_addr[a] = max(by_addr.get(a, t), t)
            await cur.executemany(
                """insert into addresses (address, source, last_fill) values (%s, %s, %s)
                   on conflict (address) do update set last_fill = greatest(addresses.last_fill, excluded.last_fill)""",
                [(a, batch[0].source, t) for a, t in by_addr.items()],
            )
            await cur.executemany(
                """insert into address_dexes (address, dex, last_fill) values (%s, %s, %s)
                   on conflict (address, dex) do update set last_fill = greatest(address_dexes.last_fill, excluded.last_fill)""",
                [(a, d, t) for (a, d), t in last_by_addr_dex.items()],
            )
        self.stats["fills"] += len(batch)
        for k in last_by_addr_dex:
            if k not in self.addr_dex_seen:
                self.addr_dex_seen.add(k)
                self.addr_seen.add(k[0])
                self.discover_q.put_nowait(k)
                self.stats["discovered"] += 1

    # ---- snapshots -----------------------------------------------------

    def parse_positions(self, state: dict, dex: str) -> list[tuple]:
        """(coin, size, entry_px, leverage, liq_px, margin_type, margin_used, position_value)
        for every nonzero position in the complex on `dex`."""
        out = []
        for ap in state.get("assetPositions") or []:
            p = ap.get("position") or {}
            coin = p.get("coin")
            if coin not in self.universe.markets or self.universe.markets[coin].dex != dex:
                continue
            size = float(p.get("szi") or 0)
            if size == 0:
                continue
            lev = p.get("leverage") or {}
            out.append(
                (
                    coin,
                    size,
                    _f(p.get("entryPx")),
                    _f(lev.get("value")),
                    _f(p.get("liquidationPx")),
                    lev.get("type"),
                    _f(p.get("marginUsed")),
                    _f(p.get("positionValue")),
                )
            )
        return out

    def set_running(self, address: str, dex: str, positions: list[tuple]) -> None:
        for coin in self.universe.coins_on(dex):
            self.positions.pop((address, coin), None)
            self.liq_px.pop((address, coin), None)
        for coin, size, _entry, _lev, liq, *_ in positions:
            self.positions[(address, coin)] = size
            if liq:
                self.liq_px[(address, coin)] = liq
        self.known[dex].add(address)

    async def discover_loop(self) -> None:
        async def worker() -> None:
            while True:
                addr, dex = await self.discover_q.get()
                try:
                    st = await self.info.clearinghouse_state(addr, dex, HIGH)
                    self.set_running(addr, dex, self.parse_positions(st, dex))
                except Exception as e:
                    log.warning("discovery snapshot %s/%s failed: %s", addr, dex, e)

        await asyncio.gather(*(worker() for _ in range(BACKGROUND_CONCURRENCY)))

    async def sweep_loop(self) -> None:
        offset = float(self.s.t("indexer", "sweep_offset_s"))
        while True:
            now = utcnow()
            hour = floor_hour(now)
            done = await self.q("select finished_at from sweeps where hour = %s", (hour,))
            if not (done and done[0]["finished_at"]):
                await self.sweep(hour)
                self.launch_metrics()
            nxt = hour + timedelta(hours=1, seconds=offset)
            await asyncio.sleep(max(1.0, (nxt - utcnow()).total_seconds()))

    async def sweep_set(self, unswept_hour: datetime | None = None) -> list[tuple[str, str]]:
        """Address-dex pairs to sweep; with `unswept_hour`, only those not yet swept for that hour."""
        days = float(self.s.t("indexer", "inactive_days"))
        cutoff = utcnow() - timedelta(days=days)
        rows = await self.q(
            """select address, dex from address_dexes
               where not seed_pending and (last_fill >= %s or last_position_hour >= %s)
                 and (%s::timestamptz is null or swept_hour is distinct from %s::timestamptz)""",
            (cutoff, cutoff, unswept_hour, unswept_hour),
        )
        dexes = set(self.universe.dexes)
        return [(r["address"], r["dex"]) for r in rows if r["dex"] in dexes]

    async def sweep(self, hour: datetime) -> None:
        """The whole sweep in one go (the box)."""
        while await self.sweep_step(hour, deadline=None) is False:
            pass

    async def sweep_step(self, hour: datetime, deadline: float | None) -> bool | None:
        """Sweep `hour` until it is finished or `deadline` (a time.monotonic() value) is
        near. Resumable: progress is committed per chunk, so the next call
        takes only the pairs not yet swept. True when the hour is finished,
        False when stopped for time, None when another process holds the sweep."""
        got = await self.q("select pg_try_advisory_lock(%s) as ok", (SWEEP_LOCK,))
        if not got[0]["ok"]:
            log.info("another process is sweeping; leaving it")
            return None
        try:
            return await self._sweep_step(hour, deadline)
        finally:
            await self.q("select pg_advisory_unlock(%s)", (SWEEP_LOCK,))

    async def _sweep_step(self, hour: datetime, deadline: float | None) -> bool:
        row = await self.q("select started_at, finished_at from sweeps where hour = %s", (hour,))
        if row and row[0]["finished_at"]:
            return True
        if not row:
            await self.start_sweep(hour)
        targets = await self.sweep_set(unswept_hour=hour)
        log.info("sweep %s: %d address-dex pairs to go", hour.isoformat(), len(targets))
        for i in range(0, len(targets), SWEEP_CHUNK):
            # A chunk takes SWEEP_CHUNK * 2 weight; leave time to write it.
            need = SWEEP_CHUNK * 2 / self.bucket.rate + 20
            if deadline is not None and _clock() + need > deadline:
                log.info("sweep %s: stopping for time with %d pairs left", hour.isoformat(), len(targets) - i)
                return False
            await self.sweep_chunk(hour, targets[i : i + SWEEP_CHUNK])
        await self.finish_sweep(hour)
        return True

    async def start_sweep(self, hour: datetime) -> None:
        # A sweep that never finished (its invocations failed) is closed as it
        # stands; metrics then computes it at whatever coverage it reached.
        await self.q(
            "update sweeps set finished_at = now() where finished_at is null and hour < %s",
            (hour,),
        )
        async with self.db_lock:
            await self.db.execute(
                """insert into sweeps (hour, started_at, addresses, requests, positions, errors)
                   values (%s, %s, 0, 0, 0, 0)
                   on conflict (hour) do update set started_at = excluded.started_at""",
                (hour, utcnow()),
            )
            await self.db.execute("delete from snapshots where hour = %s", (hour,))
            await self.db.execute("delete from accounts where hour = %s", (hour,))
        ctx_rows = []
        for dex in self.universe.dexes:
            meta, ctxs = await self.info.meta_and_ctxs(dex)
            for asset, c in zip(meta["universe"], ctxs, strict=True):
                coin = asset["name"]
                if coin not in self.universe.markets or not c.get("markPx"):
                    continue
                self.marks[coin] = float(c["markPx"])
                ctx_rows.append(
                    (
                        coin,
                        hour,
                        float(c["openInterest"]),
                        float(c["markPx"]),
                        _f(c.get("oraclePx")),
                        _f(c.get("funding")),
                        _f(c.get("premium")),
                        _f(c.get("dayNtlVlm")),
                    )
                )
        async with self.db_lock, self.db.cursor() as cur:
            await cur.executemany(
                """insert into market_hours (coin, hour, oi, mark, oracle, funding, premium, day_ntl_vlm)
                   values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (coin, hour) do update set
                   oi = excluded.oi, mark = excluded.mark, oracle = excluded.oracle, funding = excluded.funding,
                   premium = excluded.premium, day_ntl_vlm = excluded.day_ntl_vlm""",
                ctx_rows,
            )

    async def sweep_chunk(self, hour: datetime, targets: list[tuple[str, str]]) -> None:
        sem = asyncio.Semaphore(SWEEP_CONCURRENCY)
        snap_rows: list[tuple] = []
        acct_rows: list[tuple] = []
        with_pos: list[tuple[str, str]] = []
        swept: list[tuple[str, str]] = []
        errors = 0

        async def one(addr: str, dex: str) -> None:
            nonlocal errors
            async with sem:
                try:
                    st = await self.info.clearinghouse_state(addr, dex, HIGH)
                except Exception as e:
                    errors += 1
                    log.debug("sweep %s/%s failed: %s", addr, dex, e)
                    return
            ps = self.parse_positions(st, dex)
            self.set_running(addr, dex, ps)
            swept.append((addr, dex))
            if ps:
                with_pos.append((addr, dex))
                acct = account_row(st, ps)
                if acct:
                    acct_rows.append((hour, addr, dex, *acct))
            for coin, size, entry, lev, liq, mtype, mused, pval in ps:
                snap_rows.append((hour, coin, addr, size, entry, lev, liq, mtype, mused, pval))

        await asyncio.gather(*(one(a, d) for a, d in targets))
        # One transaction per chunk: snapshots and the swept marks land together.
        async with self.db_lock, self.db.transaction(), self.db.cursor() as cur:
            async with cur.copy(
                """copy snapshots (hour, coin, address, size, entry_px, leverage, liq_px,
                                   margin_type, margin_used, position_value) from stdin"""
            ) as cp:
                for r in snap_rows:
                    await cp.write_row(r)
            # Account margin lands with its positions: the stress test needs both from the same hour.
            if acct_rows:
                async with cur.copy(
                    """copy accounts (hour, address, dex, account_value, cross_value, margin_used,
                                      cross_maintenance, ntl_pos, withdrawable, margin_mode) from stdin"""
                ) as cp:
                    for r in acct_rows:
                        await cp.write_row(r)
            # Failed pairs are marked too, so a resumed sweep does not retry them forever;
            # they count as errors and lower the hour's coverage.
            await cur.executemany(
                "update address_dexes set swept_hour = %s where address = %s and dex = %s",
                [(hour, a, d) for a, d in targets],
            )
            await cur.executemany(
                "update address_dexes set first_swept_hour = coalesce(first_swept_hour, %s) where address = %s and dex = %s",
                [(hour, a, d) for a, d in swept],
            )
            await cur.executemany(
                "update address_dexes set last_position_hour = %s where address = %s and dex = %s",
                [(hour, a, d) for a, d in with_pos],
            )
            await cur.execute(
                """update sweeps set addresses = addresses + %s, requests = requests + %s,
                     positions = positions + %s, errors = errors + %s where hour = %s""",
                (len(swept), len(targets), len(snap_rows), errors, hour),
            )

    async def finish_sweep(self, hour: datetime) -> None:
        r = await self.q(
            """update sweeps set finished_at = now() where hour = %s
               returning started_at, finished_at, addresses, positions, errors""",
            (hour,),
        )
        if self.alerts_from is None:
            self.alerts_from = utcnow() + timedelta(hours=float(self.s.t("events", "warmup_hours")))
        if r:
            log.info(
                "sweep %s done in %.0fs: %d swept, %d positions, %d errors",
                hour.isoformat(),
                (r[0]["finished_at"] - r[0]["started_at"]).total_seconds(),
                r[0]["addresses"],
                r[0]["positions"],
                r[0]["errors"],
            )

    def launch_metrics(self) -> None:
        if os.environ.get("TAPE_METRICS_AFTER_SWEEP", "1") != "1":
            return

        async def run() -> None:
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "tape.cli", "metrics")
            rc = await proc.wait()
            if rc:
                log.error("metrics job exited %d", rc)
            await self.refresh_whales()

        asyncio.create_task(run())

    # ---- background work (low priority) --------------------------------

    async def history_loop(self) -> None:
        while True:
            coins = set(self.universe.coins) | set(self.s.universe.candidates)
            rows = await self.q(
                """select a.address from addresses a
                   left join lateral (select max(abs(s.size)) as m from snapshots s
                                      where s.address = a.address
                                        and s.hour = (select max(hour) from sweeps where finished_at is not null)) p on true
                   where a.history_checked_at is null
                   order by p.m desc nulls last, a.last_fill desc nulls last limit 25"""
            )
            if not rows:
                await asyncio.sleep(60)
                continue
            sem = asyncio.Semaphore(BACKGROUND_CONCURRENCY)

            async def one(a: str, sem=sem, coins=coins) -> None:
                async with sem:
                    await self.check_history(a, coins)

            await asyncio.gather(*(one(r["address"]) for r in rows))

    async def check_history(self, address: str, coins: set[str]) -> None:
        pages: list[list[dict]] = []
        start = 0
        try:
            for _ in range(HISTORY_PAGES):
                page = await self.info.user_fills_by_time(address, start, None, LOW)
                pages.append(page)
                if len(page) < 2000 or any(f.get("coin") in coins for f in page):
                    break
                start = int(page[-1]["time"]) + 1
        except Exception as e:
            log.debug("history %s failed: %s", address, e)
            return
        ff = estimate_first_fill(pages, coins)
        await self.q(
            """update addresses set first_fill = %s, first_fill_complete = %s, history_checked_at = now()
               where address = %s""",
            (from_ms(ff.first_fill_ms) if ff.first_fill_ms else None, ff.complete, address),
        )

    async def seed_loop(self) -> None:
        sem = asyncio.Semaphore(BACKGROUND_CONCURRENCY)

        async def one(addr: str, dex: str) -> None:
            async with sem:
                try:
                    st = await self.info.clearinghouse_state(addr, dex, LOW)
                except Exception as e:
                    log.debug("seed %s/%s failed: %s", addr, dex, e)
                    return
            ps = self.parse_positions(st, dex)
            if ps:
                self.set_running(addr, dex, ps)
                self.addr_dex_seen.add((addr, dex))
                self.stats["seeded_with_position"] += 1
            await self.q(
                "update address_dexes set seed_pending = false, last_position_hour = %s where address = %s and dex = %s",
                (floor_hour(utcnow()) if ps else None, addr, dex),
            )
            self.stats["seeded"] += 1

        while True:
            rows = await self.q("select address, dex from address_dexes where seed_pending limit 200")
            if not rows:
                await asyncio.sleep(120)
                continue
            await asyncio.gather(*(one(r["address"], r["dex"]) for r in rows))

    async def backfill(self, start_ms: int, end_ms: int) -> None:
        targets = await self.sweep_set()
        addrs = sorted({a for a, _ in targets})
        log.info("backfill %d addresses over %.0f minutes", len(addrs), (end_ms - start_ms) / 60000)
        n = 0
        for a in addrs:
            try:
                fills = await self.info.user_fills_by_time(a, start_ms, end_ms, LOW)
            except Exception as e:
                log.debug("backfill %s failed: %s", a, e)
                continue
            for raw in fills:
                if raw.get("coin") in self.universe.markets:
                    self.fill_q.put_nowait(from_user_fill(a, raw))
                    n += 1
        log.info("backfill queued %d fills", n)

    async def liq_loop(self) -> None:
        while True:
            addr, coin, t = await self.liq_q.get()
            await asyncio.sleep(2)  # let the fill settle into the user's history
            try:
                fills = await self.info.user_fills_by_time(addr, t - 5000, t + 5000, HIGH)
            except Exception as e:
                log.warning("liquidation check %s failed: %s", addr, e)
                continue
            rows = [
                (
                    f["coin"],
                    int(f["tid"]),
                    addr,
                    from_ms(int(f["time"])),
                    float(f["sz"]),
                    float(f["px"]),
                    float(f["sz"]) * float(f["px"]),
                    (f.get("liquidation") or {}).get("method"),
                )
                for f in fills
                if f.get("coin") in self.universe.markets and liquidated_user(f) == addr
            ]
            if rows:
                async with self.db_lock, self.db.cursor() as cur:
                    await cur.executemany(
                        """insert into liquidations (coin, tid, address, time, sz, px, notional, method)
                           values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing""",
                        rows,
                    )
                self.stats["liquidations"] += len(rows)

    async def alerts_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            try:
                n = await asyncio.to_thread(alerts.send_pending, self.s)
                if n:
                    log.info("sent %d alerts", n)
            except Exception:
                log.exception("alert send failed")

    async def refresh_loop(self) -> None:
        every = float(self.s.t("indexer", "universe_refresh_s"))
        last_u = time.monotonic()
        while True:
            await asyncio.sleep(600)
            await self.refresh_whales()
            if time.monotonic() - last_u > every:
                last_u = time.monotonic()
                with contextlib.suppress(Exception):
                    await self.load_universe()
            log.info(
                "stats: %s; addresses %d; queue %d; weight/min budget %.0f",
                dict(self.stats),
                len(self.addr_seen),
                self.fill_q.qsize(),
                self.bucket.capacity,
            )

    async def throttle_loop(self, full: float, during_sweep: float) -> None:
        """A shift and a sweep run as separate functions with separate buckets;
        while a sweep runs, the shift drops to its share of the budget."""
        while True:
            r = await self.q(
                """select 1 from sweeps where finished_at is null
                     and started_at > now() - interval '45 minutes' limit 1"""
            )
            want = during_sweep if r else full
            if abs(self.bucket.capacity - want) > 1e-6:
                self.bucket.set_rate(want)
                log.info("info budget now %.0f/min (%s)", want, "sweep running" if r else "no sweep")
            await asyncio.sleep(15)

    async def run_shift(self, seconds: float, overlap: float, budget: float, sweep_budget: float) -> dict:
        """One Vercel shift: the WebSocket and the background loops for
        `seconds`, then drain the fill queue and return. Shifts are scheduled
        closer together than they last, so the WebSocket is always held by at
        least one of them; background loops stop `overlap` early so two shifts
        do not work the same queues."""
        t0 = time.monotonic()
        await self.load_universe()
        await self.load_state()
        ws = asyncio.create_task(self.ws_loop())
        depth = asyncio.create_task(self.depth_loop())
        writer = asyncio.create_task(self.writer_loop())
        self.bg_tasks += [
            asyncio.create_task(c)
            for c in (
                self.discover_loop(),
                self.history_loop(),
                self.seed_loop(),
                self.liq_loop(),
                self.alerts_loop(),
                self.refresh_loop(),
                self.throttle_loop(budget, budget - sweep_budget),
            )
        ]
        await asyncio.sleep(max(0.0, t0 + seconds - overlap - time.monotonic()))
        for t in self.bg_tasks:
            t.cancel()
        await asyncio.gather(*self.bg_tasks, return_exceptions=True)
        await asyncio.sleep(max(0.0, t0 + seconds - time.monotonic()))
        ws.cancel()
        depth.cancel()
        await asyncio.gather(ws, depth, return_exceptions=True)
        drain_until = time.monotonic() + 30
        while (not self.fill_q.empty() or self.writing) and time.monotonic() < drain_until:
            await asyncio.sleep(0.25)
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        out = dict(self.stats)
        out.update(
            left_in_queue=self.fill_q.qsize(), seconds=round(time.monotonic() - t0), weight=self.info.weight_spent
        )
        log.info("shift done: %s", out)
        return out

    async def run(self) -> None:
        await self.load_universe()
        await self.load_state()
        tasks = [
            self.ws_loop(),
            self.depth_loop(),
            self.writer_loop(),
            self.sweep_loop(),
            self.discover_loop(),
            self.history_loop(),
            self.seed_loop(),
            self.liq_loop(),
            self.alerts_loop(),
            self.refresh_loop(),
        ]
        await asyncio.gather(*(asyncio.create_task(t) for t in tasks))


async def insert_events_async(conn, evs) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            """insert into events (time, coin, kind, address, size_usd, detail, dedupe_key, chart_hour)
               values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (dedupe_key) do nothing""",
            [
                (e.time, e.coin, e.kind, e.address, e.size_usd, json.dumps(e.detail), e.dedupe_key, floor_hour(e.time))
                for e in evs
            ],
        )


def account_row(state: dict, positions: list[tuple]) -> tuple | None:
    """(account_value, cross_value, margin_used, cross_maintenance, ntl_pos,
    withdrawable, margin_mode) from a clearinghouseState, or None if it has no
    margin summary."""
    ms = state.get("marginSummary")
    if not ms or ms.get("accountValue") in (None, ""):
        return None
    cms = state.get("crossMarginSummary") or {}
    types = {p[5] for p in positions}
    mode = "mixed" if len(types) > 1 else ("isolated" if types == {"isolated"} else "cross")
    return (
        float(ms["accountValue"]),
        _f(cms.get("accountValue")),
        _f(ms.get("totalMarginUsed")),
        _f(state.get("crossMaintenanceMarginUsed")),
        _f(ms.get("totalNtlPos")),
        _f(state.get("withdrawable")),
        mode,
    )


def _f(x) -> float | None:
    if x is None or x == "":
        return None
    return float(x)


async def main(settings: Settings) -> None:
    from .db import aconnect

    conn = await aconnect(settings.env.database_url)
    wconn = await aconnect(settings.env.database_url)
    ix = Indexer(settings, conn, wconn)
    try:
        await ix.run()
    finally:
        await ix.info.close()
        await conn.close()
        await wconn.close()
