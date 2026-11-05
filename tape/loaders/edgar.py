"""SEC EDGAR XBRL: filed figures for every entity with a CIK.

One request per company to data.sec.gov's companyfacts API. For each metric
in stack.edgar.metrics (config/tape.yaml) the concepts are tried in order;
the first that reports a period wins that period.

  instant concepts (cash, debt, backlog): one figure per balance-sheet date.
  duration concepts (revenue, capex): one figure per quarter. A cash-flow
  statement reports the year to date, so a quarter missing on its own is
  derived as this year-to-date less the last one (both filed; the row says so).

A value later restated keeps its period and gets a new version, with the
restating filing as its reason. A value repeated as a comparative in later
filings keeps the first filing that reported it as its source.

SEC asks automated clients to declare who they are: SEC_USER_AGENT must be
set (e.g. "Stack Engine research ops@yourdomain"); the loader refuses without it.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import httpx

from ..model.core import Quantity, Range, Source, source_id
from . import record_run

log = logging.getLogger("tape.loaders.edgar")
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FORMS = {"10-Q", "10-K", "20-F", "40-F", "10-Q/A", "10-K/A", "20-F/A"}


def filing_url(cik: int, accn: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/"


def _facts(doc: dict, concept: str, unit: str) -> list[dict]:
    ns, _, name = concept.rpartition(":")
    node = doc.get("facts", {}).get(ns or "us-gaap", {}).get(name)
    if not node:
        return []
    units = node.get("units", {})
    return [f for f in units.get(unit, []) if f.get("form") in FORMS]


def _settle(facts: list[dict]) -> dict[tuple[str | None, str], dict]:
    """One fact per period: the latest-filed value, sourced to the first
    filing that reported that value."""
    by: dict[tuple[str | None, str], list[dict]] = {}
    for f in facts:
        by.setdefault((f.get("start"), f["end"]), []).append(f)
    out = {}
    for k, fs in by.items():
        fs.sort(key=lambda f: (f["filed"], f["accn"]))
        latest = fs[-1]["val"]
        first = next(f for f in fs if f["val"] == latest)
        out[k] = {**first, "val": latest, "restated": len({f["val"] for f in fs}) > 1, "last": fs[-1]}
    return out


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def quarters(
    settled: dict[tuple[str | None, str], dict], derive: bool = True
) -> list[tuple[date, date, float, dict, str | None]]:
    """(start, end, value, fact, derivation) per quarter. `derive` fills a
    missing quarter from year-to-date figures; only for additive flows (a
    weighted-average share count is not one)."""
    out: dict[tuple[date, date], tuple[date, date, float, dict, str | None]] = {}
    for (start, end), f in settled.items():
        if start and 80 <= _days(start, end) <= 100:
            s, e = date.fromisoformat(start), date.fromisoformat(end)
            out[(s, e)] = (s, e, float(f["val"]), f, None)
    if not derive:
        return sorted(out.values(), key=lambda x: x[1])
    cumulative = sorted(((s, e), f) for (s, e), f in settled.items() if s and _days(s, e) > 100)
    for (start, end), f in cumulative:
        priors = [(e2, f2) for (s2, e2), f2 in settled.items() if s2 == start and e2 < end and _days(e2, end) <= 100]
        if not priors:
            continue
        prev_end, pf = max(priors, key=lambda x: x[0])
        qs, qe = date.fromisoformat(prev_end) + timedelta(days=1), date.fromisoformat(end)
        if (qs, qe) in out:
            continue
        out[(qs, qe)] = (
            qs, qe, float(f["val"]) - float(pf["val"]), f,
            f"derived: {start} to {end} ({f['form']}) less {start} to {prev_end} ({pf['form']})",
        )  # fmt: skip
    return sorted(out.values(), key=lambda x: x[1])


def parse(
    entity: str, cik: int, doc: dict, metrics: dict[str, Any], since: date
) -> tuple[list[Quantity], list[Source]]:
    """Filed quantities and their sources from one companyfacts document. Pure."""
    qs: list[Quantity] = []
    srcs: dict[str, Source] = {}
    for metric, m in metrics.items():
        unit_xbrl = m.get("xbrl_unit", "USD")
        taken: set[tuple[date, date]] = set()
        for concept in m["concepts"]:
            settled = _settle(_facts(doc, concept, unit_xbrl))
            if m["type"] == "instant":
                rows = [
                    (date.fromisoformat(e), date.fromisoformat(e), float(f["val"]), f, None)
                    for (s, e), f in settled.items()
                    if s is None
                ]
            else:
                rows = quarters(settled, derive=bool(m.get("additive", True)))
            for start, end, val, f, how in rows:
                if end < since or (start, end) in taken:
                    continue
                taken.add((start, end))
                url = filing_url(cik, f["accn"])
                sid = source_id(url)
                srcs[sid] = Source(
                    sid, url, f"{f['form']} filed {f['filed']}", "SEC EDGAR", date.fromisoformat(f["filed"])
                )
                last = f["last"]
                reason = f"{last['form']} filed {last['filed']} ({last['accn']})"
                if f["restated"]:
                    reason = f"restated: {reason}"
                note = f"{concept}" + (f"; {how}" if how else "")
                qs.append(
                    Quantity(
                        f"{entity}.{metric}",
                        m["unit"],
                        start,
                        end,
                        Range.point(val),
                        "filed",
                        "edgar",
                        source=sid,
                        revision_reason=reason,
                        note=note,
                    )  # fmt: skip
                )
    return qs, list(srcs.values())


def fetch(cik: int, user_agent: str) -> dict:
    r = httpx.get(FACTS_URL.format(cik=cik), headers={"user-agent": user_agent}, timeout=60)
    r.raise_for_status()
    return r.json()


def load(s) -> dict:
    """Fetch and store every entity with a CIK. Returns counts per entity."""
    from ..db import connect
    from ..modeljob import load_files, put_quantities, put_sources, register

    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        return {"skipped": "SEC_USER_AGENT is unset; SEC requires a declared user agent with a contact address"}
    cfg = s.t("stack", "edgar")
    since = date.fromisoformat(str(cfg["since"]))
    files = load_files()
    out: dict[str, Any] = {}
    with connect(s.env.database_url) as conn:
        register(conn, files)
        conn.commit()
        for e in files.stack.entities.values():
            if "cik" not in e.ids:
                continue
            try:
                doc = fetch(int(e.ids["cik"]), ua)
            except httpx.HTTPError as err:
                out[e.id] = f"error: {err}"
                continue
            qs, srcs = parse(e.id, int(e.ids["cik"]), doc, cfg["metrics"], since)
            put_sources(conn, srcs)
            out[e.id] = put_quantities(conn, qs)
            conn.commit()
            time.sleep(float(cfg.get("pause_s", 0.2)))
        record_run(conn, "edgar", date.today().isoformat(), True, str(out))
        conn.commit()
    return out
