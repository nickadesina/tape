"""Taiwan monthly company revenue: the memory and foundry pulse.

Listed Taiwanese companies report the month's revenue by the 10th of the
next month. TWSE's open data API serves the latest month for every listed
company (openapi.twse.com.tw, t187ap05_L). Run daily: a month already stored
writes nothing, so "on release days" needs no calendar. The endpoint has no
history, so the series starts the day this loader first ran; OTC (TPEx)
companies are not in it.

Months are ROC calendar ("11509" is September 2026); revenue is in thousands
of New Taiwan dollars, stored in TWD as `<entity>.revenue_monthly`.
"""

from __future__ import annotations

import calendar
from datetime import date

import httpx

from ..model.core import Quantity, Range, Source, source_id
from . import record_run

URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"


def roc_month(s: str) -> tuple[date, date]:
    y, m = int(s[:-2]) + 1911, int(s[-2:])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def roc_date(s: str) -> date:
    return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))


def parse(rows: list[dict], codes: dict[str, str]) -> tuple[list[Quantity], list[Source]]:
    """`codes` maps a TWSE code to an entity id. Pure."""
    qs: list[Quantity] = []
    srcs: dict[str, Source] = {}
    for r in rows:
        ent = codes.get(str(r.get("公司代號", "")).strip())
        if not ent:
            continue
        start, end = roc_month(r["資料年月"])
        published = roc_date(r["出表日期"]) if r.get("出表日期") else None
        url = f"{URL}#{start:%Y-%m}"
        sid = source_id(url)
        srcs[sid] = Source(
            sid, url, f"TWSE monthly revenue, listed companies, {start:%Y-%m}", "Taiwan Stock Exchange", published
        )
        val = float(r["營業收入-當月營收"]) * 1000
        note = r.get("備註")
        qs.append(
            Quantity(
                f"{ent}.revenue_monthly",
                "twd",
                start,
                end,
                Range.point(val),
                "filed",
                "taiwan",
                source=sid,
                revision_reason=f"TWSE monthly revenue report published {published}",
                note=None if note in (None, "-", "") else note,
            )  # fmt: skip
        )
    return qs, list(srcs.values())


def load(s) -> dict:
    from ..db import connect
    from ..modeljob import load_files, put_quantities, put_sources, register

    files = load_files()
    codes = {e.ids["twse"]: e.id for e in files.stack.entities.values() if "twse" in e.ids}
    r = httpx.get(URL, timeout=60)
    r.raise_for_status()
    qs, srcs = parse(r.json(), codes)
    with connect(s.env.database_url) as conn:
        register(conn, files)
        put_sources(conn, srcs)
        n = put_quantities(conn, qs)
        out = {"companies": len(qs), "written": n, "months": sorted({str(q.period_start)[:7] for q in qs})}
        record_run(conn, "taiwan", date.today().isoformat(), True, str(out))
        conn.commit()
    return out
