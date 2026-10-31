"""Normalize fills from the two places they come from.

WebSocket `trades`: one record per trade with `users: [buyer, seller]` and
`side` the aggressor's side (B buys, A sells).

`userFillsByTime`: one record per side, from that user's view: `side` is the
user's side and `crossed` is true when the user was the taker. The trade id
(`tid`) is shared by both sides, so a backfilled fill upserts into the same row
the WebSocket would have written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Fill:
    coin: str
    tid: int
    time_ms: int
    px: float
    sz: float
    side: str | None  # aggressor side
    buyer: str | None
    seller: str | None
    hash: str | None
    source: str

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.time_ms / 1000, tz=UTC)

    @property
    def notional(self) -> float:
        return self.px * self.sz


def from_ws_trade(t: dict[str, Any]) -> Fill:
    users = t.get("users") or [None, None]
    return Fill(
        coin=t["coin"],
        tid=int(t["tid"]),
        time_ms=int(t["time"]),
        px=float(t["px"]),
        sz=float(t["sz"]),
        side=t.get("side"),
        buyer=(users[0] or None) and users[0].lower(),
        seller=(users[1] or None) and users[1].lower(),
        hash=t.get("hash"),
        source="ws",
    )


def from_user_fill(user: str, f: dict[str, Any]) -> Fill:
    user = user.lower()
    user_side = f["side"]
    crossed = f.get("crossed")
    if crossed is None:
        aggressor = None
    elif crossed:
        aggressor = user_side
    else:
        aggressor = "A" if user_side == "B" else "B"
    return Fill(
        coin=f["coin"],
        tid=int(f["tid"]),
        time_ms=int(f["time"]),
        px=float(f["px"]),
        sz=float(f["sz"]),
        side=aggressor,
        buyer=user if user_side == "B" else None,
        seller=user if user_side == "A" else None,
        hash=f.get("hash"),
        source="backfill",
    )


def liquidated_user(f: dict[str, Any]) -> str | None:
    liq = f.get("liquidation")
    if not liq:
        return None
    u = liq.get("liquidatedUser")
    return u.lower() if u else None


@dataclass(frozen=True)
class FirstFill:
    first_fill_ms: int | None  # earliest fill in the complex found
    complete: bool  # the history reached the address's first fill ever


def estimate_first_fill(pages: list[list[dict[str, Any]]], coins: set[str], page_size: int = 2000) -> FirstFill:
    """Earliest complex fill across userFillsByTime pages fetched forward from startTime=0.

    Hyperliquid serves only an address's most recent 10,000 fills. The first
    page from startTime=0 is the earliest *available* page; if it is short, the
    address's whole history was available and the answer is exact. Otherwise
    older fills may have aged out and the answer is a lower bound on activity,
    not a first fill.
    """
    complete = bool(pages) and len(pages[0]) < page_size
    earliest: int | None = None
    for page in pages:
        for f in page:
            if f.get("coin") in coins:
                t = int(f["time"])
                if earliest is None or t < earliest:
                    earliest = t
        if earliest is not None:
            break
    return FirstFill(first_fill_ms=earliest, complete=complete)
