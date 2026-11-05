"""Does Hyperliquid price the open before the home exchange does?

trade[XYZ]'s equity perps keep trading when the underlying's exchange is shut.
In those hours the oracle has no external price and follows Hyperliquid's own
order book (docs.trade.xyz, "Oracle Price": internal pricing). So for every
closure we can ask: how far did Hyperliquid move while the home market was
shut, and how much of the opening gap had it already priced?

For each market and each closure:

  ref   Hyperliquid's price when external pricing stopped (the oracle is
        anchored to the home market up to that moment)
  pre   Hyperliquid's price at a cut-off before the home market's own pre-open
        price discovery (auctions, pre-market venues) becomes visible, so the
        move is Hyperliquid's alone
  open  the home exchange's official opening price, converted to USD at the
        hourly FX rate (Yahoo Finance)

  m = ln(pre / ref)   what Hyperliquid priced while the market was shut
  g = ln(open / ref)  the gap the home market opened with

Scored against the naive forecast "no change overnight": the share of the gap
priced in advance is 1 - sum((g - m)^2) / sum(g^2). A proxy that trades on an
external venue through the same hours (an ADR, a US peer) is the baseline:
does Hyperliquid know more than that?

Run: python research/reopen.py   (caches downloads under var/research/)
"""

from __future__ import annotations

import json
import math
import sys
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "var" / "research"
OUT = ROOT / "research" / "reopen"
HL = "https://api.hyperliquid.xyz/info"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
UA = {"user-agent": "Mozilla/5.0 (research script)"}
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class Market:
    coin: str
    label: str
    tz: str
    yahoo: str
    fx: str | None  # Yahoo FX symbol quoting local currency per USD; None for USD
    ext_close: str  # local time external pricing stops (HH:MM)
    pre_cut: str  # local time of Hyperliquid's last "own" price before the open
    open: str  # local time of the official open
    proxy: str | None  # a Hyperliquid market priced externally through the closure
    proxy_label: str | None = None
    close: str = ""  # local time of the official close (Yahoo's daily close)
    hl_close: str = ""  # local time to read Hyperliquid at that close (external pricing still on)


# Session times from docs.trade.xyz (Stocks: Korea, Japan, China, Hong Kong, US).
# pre_cut sits before each market's visible pre-open discovery: Nextrade's
# 08:00 pre-market and KRX's 08:30 auction (Korea), Japannext's 08:20 open
# (Japan), the 09:15 call auction (Shanghai), the 09:00 pre-opening session
# (Hong Kong), the 04:00 pre-market (NCLD, which xyz prices in regular hours only).
MARKETS = [
    Market(
        "xyz:SKHX",
        "SK hynix",
        "Asia/Seoul",
        "000660.KS",
        "KRW=X",
        "20:00",
        "08:00",
        "09:00",
        "xyz:SKHY",
        "SK hynix ADS",
        "15:30",
        "15:20",
    ),
    Market(
        "xyz:SMSN",
        "Samsung",
        "Asia/Seoul",
        "005930.KS",
        "KRW=X",
        "20:00",
        "08:00",
        "09:00",
        "xyz:MU",
        "Micron",
        "15:30",
        "15:20",
    ),
    Market(
        "xyz:KIOXIA",
        "Kioxia",
        "Asia/Tokyo",
        "285A.T",
        "JPY=X",
        "23:59",
        "08:15",
        "09:00",
        "xyz:SNDK",
        "Sandisk",
        "15:30",
        "15:30",
    ),
    Market(
        "xyz:CXMT",
        "CXMT",
        "Asia/Shanghai",
        "688825.SS",
        "CNH=X",
        "14:57",
        "09:15",
        "09:30",
        "xyz:MU",
        "Micron",
        "15:00",
        "14:57",
    ),
    Market(
        "xyz:MINIMAX",
        "MiniMax",
        "Asia/Hong_Kong",
        "0100.HK",
        "HKD=X",
        "16:00",
        "09:00",
        "09:30",
        None,
        None,
        "16:00",
        "16:00",
    ),
    Market(
        "xyz:ZHIPU",
        "Zhipu",
        "Asia/Hong_Kong",
        "2513.HK",
        "HKD=X",
        "16:00",
        "09:00",
        "09:30",
        None,
        None,
        "16:00",
        "16:00",
    ),
    Market(
        "xyz:NCLD",
        "Neocloud ETF",
        "America/New_York",
        "NCLD",
        None,
        "16:00",
        "04:00",
        "09:30",
        "xyz:CRWV",
        "CoreWeave",
        "16:00",
        "16:00",
    ),
]

# US names: xyz prices them externally 24/5, so Hyperliquid alone sets the
# price only from Friday 20:00 to Sunday 20:05 ET.
DROPPED: dict[str, int] = {}

US_WEEKEND = ["xyz:NVDA", "xyz:MU", "xyz:SNDK", "xyz:SKHY", "xyz:AMD", "xyz:AVGO", "xyz:TSM", "xyz:CRWV", "xyz:DRAM"]


# ---- data ------------------------------------------------------------------


def _cached(name: str, fetch) -> object:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists() and time.time() - p.stat().st_mtime < 6 * 3600:
        return json.loads(p.read_text())
    data = fetch()
    p.write_text(json.dumps(data))
    return data


def hl_series(coin: str) -> tuple[list[int], list[float]]:
    """(end_ms, close) of every 15m and 1h candle Hyperliquid serves, merged.
    A candle exists only for an interval with trades; its close is the last trade."""
    pts: dict[int, float] = {}
    for iv, ms in (("1h", 3_600_000), ("15m", 900_000)):

        def fetch(iv=iv):
            r = httpx.post(
                HL,
                json={
                    "type": "candleSnapshot",
                    "req": {"coin": coin, "interval": iv, "startTime": 0, "endTime": int(time.time() * 1000)},
                },
                timeout=60,
            )
            r.raise_for_status()
            time.sleep(0.5)
            return r.json()

        for c in _cached(f"hl_{coin.replace(':', '_')}_{iv}.json", fetch):
            pts[int(c["t"]) + ms] = float(c["c"])
    ts = sorted(pts)
    return ts, [pts[t] for t in ts]


def yahoo(sym: str, interval: str) -> list[tuple[int, float | None, float | None]]:
    def fetch():
        r = httpx.get(YAHOO.format(sym=sym), params={"interval": interval, "range": "1y"}, headers=UA, timeout=60)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        return [[t, o, c] for t, o, c in zip(res.get("timestamp") or [], q["open"], q["close"], strict=True)]

    return [tuple(x) for x in _cached(f"y_{sym}_{interval}.json", fetch)]


class Series:
    def __init__(self, ts: list[int], px: list[float]):
        self.ts, self.px = ts, px

    def before(self, t_ms: int, max_age_h: float = 72) -> float | None:
        """Last close at or before t (None if none within max_age_h)."""
        i = bisect_right(self.ts, t_ms) - 1
        if i < 0 or t_ms - self.ts[i] > max_age_h * 3_600_000:
            return None
        return self.px[i]

    def first(self) -> int | None:
        return self.ts[0] if self.ts else None


class Fx:
    """Hourly FX, local currency per USD."""

    def __init__(self, sym: str | None):
        self.rows = [(t * 1000, c) for t, _o, c in yahoo(sym, "1h") if c] if sym else []
        self.ts = [t for t, _ in self.rows]

    def at(self, t_ms: int) -> float | None:
        if not self.rows:
            return 1.0
        i = bisect_right(self.ts, t_ms) - 1
        if i < 0 or t_ms - self.ts[i] > 6 * 3_600_000:
            return None
        return self.rows[i][1]


def _at(d: date, hhmm: str, tz: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


# ---- windows ---------------------------------------------------------------


@dataclass
class Window:
    market: str
    day: str  # local date of the open
    m: float  # Hyperliquid's move while shut
    g: float  # opening gap
    p: float | None  # proxy's move over the same hours
    m_ext: float | None = None  # Hyperliquid's move from the official close to pre
    g_ext: float | None = None  # the gap from the official close (both ends from the exchange, none from HL)
    close_diff: float | None = None  # ln(HL at the close / official close in USD): conversion check


def asia_windows(mk: Market) -> list[Window]:
    hl = Series(*hl_series(mk.coin))
    proxy = Series(*hl_series(mk.proxy)) if mk.proxy else None
    fx = Fx(mk.fx)
    days = []
    for t, o, c in yahoo(mk.yahoo, "1d"):
        if o and c:
            days.append((datetime.fromtimestamp(t, ZoneInfo(mk.tz)).date(), o, c))
    out = []
    for (d0, _, close_local), (d1, open_local, _) in zip(days, days[1:], strict=False):
        t_ref = _at(d0, mk.ext_close, mk.tz)
        t_pre = _at(d1, mk.pre_cut, mk.tz)
        t_open = _at(d1, mk.open, mk.tz)
        if hl.first() is None or t_ref < hl.first():
            continue
        ref, pre = hl.before(t_ref, 6), hl.before(t_pre)
        rate = fx.at(t_open)
        if not (ref and pre and rate):
            continue
        g = math.log(open_local / rate / ref)
        if abs(g) > 0.5:  # a split or a bad print, not a gap
            continue
        p = None
        if proxy:
            p0, p1 = proxy.before(t_ref, 6), proxy.before(t_pre, 6)
            p = math.log(p1 / p0) if p0 and p1 else None
        t_close = _at(d0, mk.close, mk.tz)
        hl_c = hl.before(_at(d0, mk.hl_close, mk.tz), 1)
        rate_c = fx.at(t_close)
        close_usd = close_local / rate_c if rate_c else None
        w = Window(mk.coin, d1.isoformat(), math.log(pre / ref), g, p)
        if close_usd and hl_c:
            w.m_ext = math.log(pre / hl_c)
            w.g_ext = math.log(open_local / rate / close_usd)
            w.close_diff = math.log(hl_c / close_usd)
            if abs(w.close_diff) > 0.10:  # the perp was not tracking the home market (contract change, bad data)
                DROPPED[mk.coin] = DROPPED.get(mk.coin, 0) + 1
                continue
        out.append(w)
    return out


def us_weekend_windows(coin: str) -> list[Window]:
    hl = Series(*hl_series(coin))
    sym = coin.split(":")[1]
    tz = "America/New_York"
    out = []
    for t, o, _c in yahoo(sym, "1d"):
        d = datetime.fromtimestamp(t, ZoneInfo(tz)).date()
        if d.weekday() != 0 or not o:  # Monday opens only
            continue
        fri, sun = d - timedelta(days=3), d - timedelta(days=1)
        t_ref, t_pre = _at(fri, "20:00", tz), _at(sun, "20:00", tz)
        if hl.first() is None or t_ref < hl.first():
            continue
        ref, pre = hl.before(t_ref, 6), hl.before(t_pre)
        if not (ref and pre):
            continue
        g = math.log(o / ref)
        if abs(g) > 0.5:
            continue
        out.append(Window(coin, d.isoformat(), math.log(pre / ref), g, None))
    return out


# ---- scoring ---------------------------------------------------------------


def score(ws: list[Window], key: str = "m") -> dict:
    xs = [getattr(w, key) for w in ws if getattr(w, key) is not None]
    gs = [w.g for w in ws if getattr(w, key) is not None]
    n = len(xs)
    if n < 5:
        return {"n": n}
    sxx = sum(x * x for x in xs)
    sgg = sum(g * g for g in gs)
    sxg = sum(x * g for x, g in zip(xs, gs, strict=True))
    mx, mg = sum(xs) / n, sum(gs) / n
    cov = sum((x - mx) * (g - mg) for x, g in zip(xs, gs, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vg = sum((g - mg) ** 2 for g in gs)
    big = [(x, g) for x, g in zip(xs, gs, strict=True) if abs(g) >= 0.01]
    return {
        "n": n,
        "share_priced": 1 - sum((g - x) ** 2 for x, g in zip(xs, gs, strict=True)) / sgg if sgg else None,
        "beta": sxg / sxx if sxx else None,  # gap per unit of overnight move, through the origin
        "corr": cov / math.sqrt(vx * vg) if vx and vg else None,
        "hit_rate_big_gaps": sum(1 for x, g in big if x * g > 0) / len(big) if big else None,
        "n_big_gaps": len(big),
        "mean_abs_gap": sum(abs(g) for g in gs) / n,
        "mean_abs_miss": sum(abs(g - x) for x, g in zip(xs, gs, strict=True)) / n,
    }


def proxy_score(ws: list[Window]) -> dict:
    """The baseline: the proxy's move, scaled by its in-sample beta (generous to the proxy)."""
    pairs = [(w.p, w.g, w.m) for w in ws if w.p is not None]
    if len(pairs) < 5:
        return {"n": len(pairs)}
    spp = sum(p * p for p, _, _ in pairs)
    b = sum(p * g for p, g, _ in pairs) / spp if spp else 0.0
    sgg = sum(g * g for _, g, _ in pairs)
    proxy_share = 1 - sum((g - b * p) ** 2 for p, g, _ in pairs) / sgg
    hl_share = 1 - sum((g - m) ** 2 for _, g, m in pairs) / sgg
    # Does Hyperliquid add to the proxy? Regress the proxy's miss on HL's move beyond the proxy.
    res = [(g - b * p, m - b * p) for p, g, m in pairs]
    sxx = sum(x * x for _, x in res)
    k = sum(r * x for r, x in res) / sxx if sxx else 0.0
    both_share = 1 - sum((r - k * x) ** 2 for r, x in res) / sgg
    return {
        "n": len(pairs),
        "proxy_beta": b,
        "proxy_share": proxy_share,
        "hl_share_same_days": hl_share,
        "proxy_plus_hl_share": both_share,
    }


def check_close(ws: list[Window]) -> dict:
    """Hyperliquid at the official close against the close converted to USD.
    Agreement means the FX conversion is right and the perp tracks the home
    market while it trades."""
    d = sorted(abs(w.close_diff) for w in ws if w.close_diff is not None)
    if not d:
        return {"n": 0}
    return {"n": len(d), "median_abs_diff": d[len(d) // 2], "p90_abs_diff": d[int(len(d) * 0.9)]}


def score_ext(ws: list[Window]) -> dict:
    """The same score with the gap measured from the official close, so no
    Hyperliquid price sits in both the move and the gap."""
    sub_ws = [Window(w.market, w.day, w.m_ext, w.g_ext, None) for w in ws if w.g_ext is not None]
    return score(sub_ws)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict = {"generated": datetime.now(UTC).isoformat(timespec="minutes"), "asia": {}, "us_weekend": {}}
    all_windows: list[dict] = []
    for mk in MARKETS:
        try:
            ws = asia_windows(mk)
        except Exception as e:  # a market without data is reported, not fatal
            print(f"{mk.coin}: {e}", file=sys.stderr)
            continue
        r = {
            "label": mk.label,
            "proxy": mk.proxy_label,
            **score(ws),
            "baseline": proxy_score(ws),
            "from_official_close": score_ext(ws),
            "close_check": check_close(ws),
        }
        if ws:
            r["from"], r["to"] = ws[0].day, ws[-1].day
        results["asia"][mk.coin] = r
        all_windows += [w.__dict__ for w in ws]
        print(f"{mk.coin:12s} {json.dumps(r, default=lambda x: round(x, 3))}")
    pooled: list[Window] = []
    for coin in US_WEEKEND:
        try:
            ws = us_weekend_windows(coin)
        except Exception as e:
            print(f"{coin}: {e}", file=sys.stderr)
            continue
        pooled += ws
        results["us_weekend"][coin] = score(ws)
        all_windows += [w.__dict__ for w in ws]
    results["us_weekend"]["pooled"] = score(pooled)
    results["dropped_not_tracking_at_close"] = DROPPED
    print("US weekends pooled", json.dumps(results["us_weekend"]["pooled"], default=lambda x: round(x, 3)))
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=lambda x: round(x, 5)))
    (OUT / "windows.json").write_text(
        json.dumps(all_windows, default=lambda x: round(x, 6) if isinstance(x, float) else x)
    )


if __name__ == "__main__":
    main()
