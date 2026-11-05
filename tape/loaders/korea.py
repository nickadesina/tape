"""Korea's semiconductor exports, monthly, by HS code.

Korea Customs Service publishes trade statistics on the public data portal
(apis.data.go.kr, service 1220000, "Itemtrade": exports and imports by HS
code and month). It needs a free service key: set KOREA_DATA_KEY. The
ten-day releases are press releases, not an API, so they are not loaded.

Stored as korea_chips.exports_monthly in USD, the sum over stack.korea.hs
(config/tape.yaml).

UNVERIFIED: the portal refused connections from the environment this was
built in, so the request and field names follow the published spec and
have not been run. The parser fails loudly, naming the fields it got, if
the response differs.
"""

from __future__ import annotations

import calendar
import os
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from ..model.core import Quantity, Range, Source, source_id
from . import record_run

URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


def parse(xml_text: str, hs: list[str]) -> dict[tuple[int, int], float]:
    """USD exports per (year, month), summed over items whose HS code starts
    with one of `hs`. Pure."""
    root = ET.fromstring(xml_text)
    code = root.findtext(".//resultCode")
    if code not in (None, "00"):
        raise ValueError(f"Korea customs API error {code}: {root.findtext('.//resultMsg')}")
    out: dict[tuple[int, int], float] = {}
    for it in root.iter("item"):
        fields = {c.tag: (c.text or "").strip() for c in it}
        if not {"year", "hsCd", "expDlr"} <= set(fields):
            raise ValueError(f"unexpected item fields: {sorted(fields)}")
        if fields["year"] in ("", "총계") or not any(fields["hsCd"].startswith(h) for h in hs):
            continue
        y, m = fields["year"].split(".")
        out[(int(y), int(m))] = out.get((int(y), int(m)), 0.0) + float(fields["expDlr"].replace(",", ""))
    return out


def to_quantities(monthly: dict[tuple[int, int], float], hs: list[str]) -> tuple[list[Quantity], list[Source]]:
    qs, srcs = [], {}
    for (y, m), usd in sorted(monthly.items()):
        url = f"{URL}?hsSgn={','.join(hs)}#{y}-{m:02d}"
        sid = source_id(url)
        srcs[sid] = Source(
            sid,
            url,
            f"Korea Customs Service trade statistics, HS {', '.join(hs)}, {y}-{m:02d}",
            "Korea Customs Service",
        )
        qs.append(
            Quantity(
                "korea_chips.exports_monthly",
                "usd",
                date(y, m, 1),
                date(y, m, calendar.monthrange(y, m)[1]),
                Range.point(usd),
                "filed",
                "korea",
                source=sid,
                revision_reason="Korea Customs Service revision",
            )  # fmt: skip
        )
    return qs, list(srcs.values())


def load(s, months: int = 13) -> dict:
    from ..db import connect
    from ..modeljob import load_files, put_quantities, put_sources, register

    key = os.environ.get("KOREA_DATA_KEY")
    if not key:
        return {"skipped": "KOREA_DATA_KEY is unset (a free service key from data.go.kr)"}
    hs = [str(h) for h in s.t("stack", "korea", "hs")]
    today = date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    monthly: dict[tuple[int, int], float] = {}
    for h in hs:
        r = httpx.get(
            URL,
            params={"serviceKey": key, "strtYymm": f"{y}{m:02d}", "endYymm": f"{today:%Y%m}", "hsSgn": h},
            timeout=60,
        )
        r.raise_for_status()
        for k, v in parse(r.text, [h]).items():
            monthly[k] = monthly.get(k, 0.0) + v
    qs, srcs = to_quantities(monthly, hs)
    files = load_files()
    with connect(s.env.database_url) as conn:
        register(conn, files)
        put_sources(conn, srcs)
        n = put_quantities(conn, qs)
        out = {"months": len(qs), "written": n}
        record_run(conn, "korea", today.isoformat(), True, str(out))
        conn.commit()
    return out
