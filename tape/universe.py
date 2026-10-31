"""Resolve the configured AI complex against live Hyperliquid meta.

The config says which leg a market belongs to; the chain says whether it is
listed. A market is in the universe only if both agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .settings import UniverseConfig


@dataclass(frozen=True)
class Market:
    coin: str
    dex: str
    leg: str
    max_leverage: int
    sz_decimals: int


@dataclass
class Universe:
    markets: dict[str, Market]
    missing: list[str] = field(default_factory=list)       # configured, not listed on chain
    delisted: list[str] = field(default_factory=list)      # configured, listed but delisted
    candidates: list[str] = field(default_factory=list)    # live, named as candidates in config
    unclassified: list[str] = field(default_factory=list)  # live, in neither legs nor candidates

    @property
    def coins(self) -> list[str]:
        return sorted(self.markets)

    @property
    def dexes(self) -> list[str]:
        return sorted({m.dex for m in self.markets.values()})

    def coins_on(self, dex: str) -> list[str]:
        return sorted(c for c, m in self.markets.items() if m.dex == dex)

    def leg_markets(self, leg: str) -> list[str]:
        return sorted(c for c, m in self.markets.items() if m.leg == leg)


def dex_of(coin: str) -> str:
    if ":" not in coin:
        return ""
    return coin.split(":", 1)[0]


def resolve(cfg: UniverseConfig, metas: dict[str, list[Any]]) -> Universe:
    """`metas` maps dex name to its metaAndAssetCtxs (or meta) response.

    A dex absent from `metas` (e.g. perpDexs no longer lists it) leaves all its
    configured markets in `missing`.
    """
    live: dict[str, dict[str, Any]] = {}
    for dex, resp in metas.items():
        meta = resp[0] if isinstance(resp, list) else resp
        for asset in meta["universe"]:
            live[asset["name"]] = {**asset, "_dex": dex}

    configured = {m: leg.key for leg in cfg.legs.values() for m in leg.markets}
    u = Universe(markets={})
    for coin, leg in configured.items():
        a = live.get(coin)
        if a is None:
            u.missing.append(coin)
        elif a.get("isDelisted"):
            u.delisted.append(coin)
        else:
            u.markets[coin] = Market(
                coin=coin,
                dex=a["_dex"],
                leg=leg,
                max_leverage=int(a.get("maxLeverage") or 0),
                sz_decimals=int(a.get("szDecimals") or 0),
            )
    cands = set(cfg.candidates)
    for coin, a in sorted(live.items()):
        if coin in configured or a.get("isDelisted"):
            continue
        (u.candidates if coin in cands else u.unclassified).append(coin)
    u.missing.sort()
    u.delisted.sort()
    return u


def dexes_in_perp_dexs(perp_dexs: list[Any]) -> set[str]:
    return {d["name"] for d in perp_dexs if d}
