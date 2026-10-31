"""Hyperliquid info endpoint client under the published per-IP weight limit.

Weights (Hyperliquid docs, "Rate limits and user limits"): clearinghouseState,
allMids, l2Book, orderStatus, spotClearinghouseState and exchangeStatus cost 2;
userRole costs 60; everything else costs 20. Fill and funding history requests
cost an extra 1 per 20 items returned. The limit is 1200 weight per minute.

The bucket has two priorities. High priority (the hourly sweep, market
contexts, liquidation confirmations) may drain it; low priority (history
backfill for new addresses, leaderboard seeding) waits while the bucket is
below a reserve, so background work never delays the sweep.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

LIGHT = {"clearinghouseState", "allMids", "l2Book", "orderStatus", "spotClearinghouseState", "exchangeStatus"}
PER_ITEM_20 = {
    "recentTrades",
    "historicalOrders",
    "userFills",
    "userFillsByTime",
    "fundingHistory",
    "userFunding",
    "nonUserFundingUpdates",
    "twapHistory",
    "userTwapSliceFills",
    "userTwapSliceFillsByTime",
    "delegatorHistory",
    "delegatorRewards",
    "validatorStats",
}

HIGH = 0
LOW = 1


def base_weight(req_type: str) -> int:
    if req_type in LIGHT:
        return 2
    if req_type == "userRole":
        return 60
    return 20


def extra_weight(req_type: str, n_items: int) -> int:
    if req_type in PER_ITEM_20:
        return n_items // 20
    return 0


class WeightBucket:
    """Token bucket refilled continuously at `per_minute / 60` tokens a second.

    Tokens can go negative when a response carries more per-item weight than
    was reserved; later callers then wait for the debt to refill.
    """

    def __init__(self, per_minute: float, low_reserve: float = 0.3, clock=time.monotonic):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self.low_floor = self.capacity * low_reserve
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def wait_time(self, weight: float, priority: int = HIGH) -> float:
        """Seconds until `weight` can be taken at `priority` (0 if now)."""
        self._refill()
        floor = self.low_floor if priority == LOW else 0.0
        need = weight + floor - self.tokens
        return 0.0 if need <= 0 else need / self.rate

    async def acquire(self, weight: float, priority: int = HIGH) -> None:
        while True:
            async with self._lock:
                wait = self.wait_time(weight, priority)
                if wait == 0.0:
                    self.tokens -= weight
                    return
            await asyncio.sleep(min(wait, 5.0))

    def charge(self, weight: float) -> None:
        self._refill()
        self.tokens -= weight


class InfoClient:
    def __init__(self, url: str, bucket: WeightBucket, timeout: float = 20.0):
        self.url = url
        self.bucket = bucket
        self.http = httpx.AsyncClient(timeout=timeout, headers={"content-type": "application/json"})
        self.weight_spent = 0

    async def close(self) -> None:
        await self.http.aclose()

    async def post(self, body: dict[str, Any], priority: int = HIGH, retries: int = 4) -> Any:
        req_type = body["type"]
        w = base_weight(req_type)
        delay = 2.0
        for attempt in range(retries + 1):
            await self.bucket.acquire(w, priority)
            self.weight_spent += w
            try:
                r = await self.http.post(self.url, json=body)
                if r.status_code == 429:
                    log.warning("429 from info endpoint on %s; backing off %.0fs", req_type, delay * 5)
                    self.bucket.charge(self.bucket.capacity / 4)
                    await asyncio.sleep(delay * 5)
                    delay *= 2
                    continue
                r.raise_for_status()
                data = r.json()
                if isinstance(data, list):
                    extra = extra_weight(req_type, len(data))
                    if extra:
                        self.bucket.charge(extra)
                        self.weight_spent += extra
                return data
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == retries:
                    raise
                log.warning("info %s failed (%s); retry in %.0fs", req_type, e, delay)
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    # Typed helpers. Each returns the endpoint's JSON unchanged.

    async def perp_dexs(self) -> list[Any]:
        return await self.post({"type": "perpDexs"})

    async def meta_and_ctxs(self, dex: str) -> list[Any]:
        return await self.post({"type": "metaAndAssetCtxs", "dex": dex})

    async def clearinghouse_state(self, user: str, dex: str, priority: int = HIGH) -> dict[str, Any]:
        return await self.post({"type": "clearinghouseState", "user": user, "dex": dex}, priority)

    async def user_fills_by_time(
        self, user: str, start_ms: int, end_ms: int | None = None, priority: int = LOW
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"type": "userFillsByTime", "user": user, "startTime": start_ms}
        if end_ms is not None:
            body["endTime"] = end_ms
        return await self.post(body, priority)
