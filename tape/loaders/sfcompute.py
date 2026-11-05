"""SF Compute's public order book, hourly: traded rents by chip and delivery
window (docs.sfcompute.com/preview/orderbook.md).

For each accelerator in stack.sfcompute.accelerators: every delivery window
with open orders over the next `horizon_days` (/windows: best bid and ask,
order counts), and the executions in windows that start within
`rent_start_within_days` (/fills). Prices are per node-hour on the book and
stored per GPU-hour (divided by `gpus_per_node`). The windows give the term
structure: scarcity now against later.

Needs SFCOMPUTE_TOKEN (a bearer token, `sf tokens create`); without it the
collector does nothing.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from . import record_run

log = logging.getLogger("tape.loaders.sfcompute")
BASE = "https://api.sfcompute.com/preview/v2/orderbook"


def _ts(x: int) -> datetime:
    return datetime.fromtimestamp(int(x), UTC)


def _nodes(side: dict | None) -> int | None:
    if not side:
        return None
    segs = [s for s in side.get("allocation_schedule_delta") or [] if s.get("end_at") is not None]
    return max((int(s["node_count"]) for s in segs), default=None)


def parse_windows(acc: str, data: list[dict], now: datetime, gpus_per_node: int) -> list[tuple]:
    """rent_quotes rows from /windows items. Pure."""
    t = now.replace(second=0, microsecond=0)
    rows = []
    for w in data:
        bid, ask = w.get("best_bid"), w.get("best_ask")
        rows.append(
            (
                t,
                acc,
                w["instance_sku"],
                _ts(w["start_at"]),
                _ts(w["end_at"]),
                float(w.get("duration_hours") or (int(w["end_at"]) - int(w["start_at"])) / 3600),
                float(bid["dollars_per_node_hour"]) / gpus_per_node if bid else None,
                float(ask["dollars_per_node_hour"]) / gpus_per_node if ask else None,
                _nodes(bid),
                _nodes(ask),
                w.get("total_bid_order_count"),
                w.get("total_ask_order_count"),
            )  # fmt: skip
        )
    return rows


def parse_fills(acc: str, start: int, end: int, data: list[dict], gpus_per_node: int) -> list[tuple]:
    return [
        (acc, f["instance_sku"], _ts(start), _ts(end), _ts(f["filled_at"]),
         float(f["dollars_per_node_hour"]) / gpus_per_node, _nodes(f) or 0)
        for f in data
    ]  # fmt: skip


class Client:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"authorization": f"Bearer {token}"})

    def pages(self, path: str, params: dict[str, Any], max_pages: int = 20) -> list[dict]:
        out: list[dict] = []
        p = dict(params)
        for _ in range(max_pages):
            r = self.http.get(f"{BASE}/{path}", params=p)
            r.raise_for_status()
            body = r.json()
            out += body.get("data") or []
            cursor = body.get("cursor") or body.get("next_cursor")
            if not body.get("has_more") or not cursor:
                break
            p["starting_after"] = cursor
        return out


def collect(s, client: Client, now: datetime | None = None) -> tuple[list[tuple], list[tuple]]:
    cfg = s.t("stack", "sfcompute")
    now = now or datetime.now(UTC)
    g = int(cfg["gpus_per_node"])
    start = int(now.timestamp()) // 60 * 60
    end = start + int(cfg["horizon_days"]) * 86400
    quotes, fills = [], []
    for acc in cfg["accelerators"]:
        req = f"accelerator:{acc}"
        ws = client.pages("windows", {"requirements": req, "range_start_at": start, "range_end_at": end, "limit": 200})
        quotes += parse_windows(acc, ws, now, g)
        soon = start + int(cfg["rent_start_within_days"]) * 86400
        for w in ws:
            if int(w["start_at"]) > soon or float(w.get("duration_hours") or 0) < float(cfg["min_window_hours"]):
                continue
            try:
                fs = client.pages(
                    "fills",
                    {"requirements": req, "start_at": w["start_at"], "end_at": w["end_at"],
                     "since_at": start - 2 * 86400, "limit": 200},
                    max_pages=5,
                )  # fmt: skip
            except httpx.HTTPStatusError as e:
                log.warning("fills %s %s: %s", acc, w["start_at"], e)
                continue
            fills += parse_fills(acc, int(w["start_at"]), int(w["end_at"]), fs, g)
    return quotes, fills


def store(conn, quotes: list[tuple], fills: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """insert into rent_quotes (time, accelerator, sku, start_at, end_at, hours, bid, ask, bid_nodes, ask_nodes,
                                        bids, asks)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing""",
            quotes,
        )
        cur.executemany(
            """insert into rent_fills (accelerator, sku, start_at, end_at, filled_at, price, nodes)
               values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing""",
            fills,
        )


def load(s) -> dict:
    from ..db import connect

    token = os.environ.get("SFCOMPUTE_TOKEN")
    if not token:
        return {"skipped": "SFCOMPUTE_TOKEN is unset"}
    now = datetime.now(UTC)
    quotes, fills = collect(s, Client(token), now)
    with connect(s.env.database_url) as conn:
        store(conn, quotes, fills)
        out = {"quotes": len(quotes), "fills": len(fills)}
        record_run(conn, "sfcompute", now.strftime("%Y-%m-%dT%HZ"), True, str(out))
        conn.commit()
    return out
