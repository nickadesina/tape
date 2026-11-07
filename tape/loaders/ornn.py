"""GPU rents: the Ornn Compute Price Index (OCPI), daily, free tier.

OCPI is a volume-weighted, winsorized mean of executed on-demand GPU rentals
in USD per GPU-hour (data.ornn.com/methodology). The free tier needs no key
and serves the trailing three months of daily values for H100 SXM, H200,
B200, A100 SXM4 and RTX PRO 6000 WS; the loader asks for that whole window
every day, so a missed day fills itself and nothing is stored twice.

LICENCE: Ornn's terms license the data for internal use only and bar
publishing it, or anything derived from it, without a written agreement
(data.ornn.com/terms, sections 2, 3 and 6). So the source is registered as
internal: every figure from it, every estimate or gap computed from one, is
private. The model and the operator's alerts use them; the site never shows
them. Publishing needs Ornn's written consent first.

Stored as `<sku>.rent_index` (one figure a day, dated by the US Eastern day
the value was stamped). The model run turns it into `<sku>.rent_traded`, a
range: the day's value with the trailing low and high around it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from ..model.core import Quantity, Range, Series, Source
from . import record_run

API = "https://api.ornnai.com/api"
ET = ZoneInfo("America/New_York")


SOURCE_ID = "ornn_ocpi"  # the same key as in config/stack/sources.yaml


def ornn_source(url: str) -> Source:
    return Source(SOURCE_ID, url, "Ornn Compute Price Index (OCPI)", "Ornn", internal=True)


def parse(sku: str, body: dict, src: Source) -> list[Quantity]:
    """`<sku>.rent_index` figures from an index-history response. Pure."""
    if not body.get("success"):
        raise ValueError(f"Ornn {sku}: {body.get('error') or body}")
    out = []
    for p in body.get("data") or []:
        t = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
        d = t.astimezone(ET).date()
        out.append(
            Quantity(
                f"{sku}.rent_index",
                "usd_per_gpu_hour",
                d,
                d,
                Range.point(float(p["index_value"])),
                "reported",
                "ornn",
                source=src.id,
                revision_reason="Ornn restated the published value",
                private=True,
            )  # fmt: skip
        )
    return out


def rent_ranges(series: Series, skus: list[str], day: date, window_days: int, src: str) -> list[Quantity]:
    """`<sku>.rent_traded` for `day`: the latest index value on or before it
    as the middle, the low and high of the trailing `window_days` as the
    range. Private, like its source. Pure."""
    out = []
    for sku in skus:
        pts = [
            q
            for q in series.by_key.get(f"{sku}.rent_index", [])
            if day - timedelta(days=window_days) < q.period_end <= day
        ]
        if not pts:
            continue
        mid = pts[-1].value.mid
        vals = [q.value.mid for q in pts]
        out.append(
            Quantity(
                f"{sku}.rent_traded",
                "usd_per_gpu_hour",
                day,
                day,
                Range(min(vals), mid, max(vals)),
                "reported",
                "ornn",
                source=src,
                private=True,
                note=f"OCPI on {pts[-1].period_end}; range: low and high of the {len(pts)} values in the last {window_days} days",
            )  # fmt: skip
        )
    return out


def load(s) -> dict:
    from ..db import connect
    from ..modeljob import load_files, put_quantities, put_sources, register

    cfg = s.t("stack", "ornn")
    src = ornn_source(cfg["source_url"])
    today = date.today()
    start = today - timedelta(days=int(cfg["history_days"]))
    qs: list[Quantity] = []
    with httpx.Client(timeout=30) as http:
        for sku, name in cfg["gpus"].items():
            r = http.get(f"{API}/gpu/{name}/index-history", params={"startDate": str(start), "endDate": str(today)})
            r.raise_for_status()
            qs += parse(sku, r.json(), src)
    files = load_files()
    with connect(s.env.database_url) as conn:
        register(conn, files)
        put_sources(conn, [src])
        n = put_quantities(conn, qs)
        out = {"values": len(qs), "written": n, "through": str(max((q.period_end for q in qs), default=None))}
        record_run(conn, "ornn", today.isoformat(), True, str(out))
        conn.commit()
    return out
