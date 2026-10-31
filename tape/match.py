"""Match a post's text to markets in the universe, for quote-ride alerts.

twitter-checker calls `tape match "<post text>"` (or imports `match_text`)
when a watched account posts; if the post is about a name in the universe the
alert carries the matching Tape chart and its permalink.
"""

from __future__ import annotations

import re

from .db import connect
from .settings import Settings, UniverseConfig
from .timeutil import hour_key


def _patterns(cfg: UniverseConfig) -> dict[str, list[re.Pattern]]:
    out: dict[str, list[re.Pattern]] = {}
    for leg in cfg.legs.values():
        for coin in leg.markets:
            ticker = coin.split(":", 1)[1]
            names = {ticker, f"${ticker}", *cfg.aliases.get(coin, ())}
            out[coin] = [re.compile(rf"(?<![\w$]){re.escape(n)}(?!\w)", re.IGNORECASE) for n in names]
    return out


def match_coins(cfg: UniverseConfig, text: str) -> list[str]:
    """Coins mentioned in `text`, most mentions first; xyz before io on ties
    (it carries the deeper book for every name both list)."""
    counts = {}
    for coin, pats in _patterns(cfg).items():
        n = sum(len(p.findall(text)) for p in pats)
        if n:
            counts[coin] = n
    return sorted(counts, key=lambda c: (-counts[c], not c.startswith("xyz:"), c))


def match_text(s: Settings, text: str) -> dict:
    coins = match_coins(s.universe, text)
    out = {"coins": coins, "charts": []}
    if not coins:
        return out
    with connect(s.env.database_url) as conn:
        for c in coins[:3]:
            r = conn.execute(
                "select hour from charts where kind = 'card' and subject = %s order by hour desc limit 1", (c,)
            ).fetchone()
            if not r:
                continue
            hk = hour_key(r["hour"])
            slug = c.replace(":", "-")
            out["charts"].append({
                "coin": c,
                "hour": hk,
                "png": f"{s.env.site_url}/c/card/{slug}/{hk}.png",
                "permalink": f"{s.env.site_url}/p/card/{slug}/{hk}",
                "market": f"{s.env.site_url}/m/{slug}",
            })
    return out
