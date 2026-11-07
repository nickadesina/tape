"""The daily model run, against Postgres (Stack Engine design doc).

  1. Load config/stack/ and config/graph.yaml; register sources and entities;
     version the graph's edges into `relationships`.
  2. Version the verified config figures into `quantities` (a changed figure
     needs a new revision_reason), and flag stale ones.
  3. Read every current figure (config, EDGAR, Taiwan, Korea) plus the day's
     the day's GPU rent range (Ornn's index, private), compute the estimates,
     version them. A figure from an internal-only source, and anything
     computed from one, is private: it reaches the operator, never the site.
  4. For each reviewed comparison: market price -> implied value, against the
     model; write `gaps`, flag the persistent ones, tell the operator.
  5. Run the scenarios; score the forecasts whose actual figure is in.

The pure parts are tape/model/. `tape model --drafts` runs everything with
drafts treated as verified and stores nothing: the way to see the first
exhibits before a person has checked their inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import yaml

from .db import connect
from .graph import Graph, load_graph_file
from .loaders import ornn
from .model import conversions as conv_mod
from .model import gaps as gaps_mod
from .model import ledger
from .model.core import (
    Missing,
    Quantity,
    Range,
    Series,
    Source,
    Stack,
    evaluate,
    load_stack,
    revision,
    source_id,
)
from .model.scenarios import Link, ScenarioSpec, load_scenarios, propagate
from .settings import CONFIG_DIR, Settings
from .telegram import Telegram
from .timeutil import utcnow

log = logging.getLogger("tape.model")
STACK_DIR = CONFIG_DIR / "stack"
FILES = ("entities.yaml", "sources.yaml", "quantities.yaml", "conversions.yaml", "scenarios.yaml", "forecasts.yaml")
MODEL_LOCK = 0x7A9E5701
PRIVATE_NOTE = (
    " PRIVATE: computed from a source licensed for internal use only (Ornn's index); not for publication"
    " without that source's written consent."
)


# ---- config files ----------------------------------------------------------


@dataclass(frozen=True)
class Comparison:
    node: str
    entity: str
    label: str
    conversion: str
    market: dict[str, str]
    inputs: dict[str, str]
    assumptions: dict[str, tuple[Range, str]]
    reviewed_at: date | None


@dataclass(frozen=True)
class Forecast:
    id: str
    key: str
    unit: str
    period_start: date
    period_end: date
    value: float
    lo: float
    hi: float
    published: date
    url: str | None
    note: str | None


@dataclass
class Files:
    graph: Graph
    stack: Stack
    comparisons: list[Comparison]
    draft_comparisons: list[Comparison]
    links: list[Link]
    scenarios: list[ScenarioSpec]
    forecasts: list[Forecast]
    hash: str


def _yaml(name: str) -> dict[str, Any]:
    with open(STACK_DIR / name) as f:
        return yaml.safe_load(f) or {}


def config_hash() -> str:
    h = hashlib.sha1()
    for name in FILES:
        h.update((STACK_DIR / name).read_bytes())
    h.update((CONFIG_DIR / "graph.yaml").read_bytes())
    return h.hexdigest()


def load_comparisons(raw: dict[str, Any], stack: Stack, drafts: bool) -> tuple[list[Comparison], list[Comparison]]:
    ok: list[Comparison] = []
    draft: list[Comparison] = []
    seen: set[str] = set()
    for x in raw.get("comparisons") or []:
        node = x.get("node")
        if not node or node in seen:
            raise ValueError(f"comparison node missing or repeated: {node!r}")
        seen.add(node)
        conv = conv_mod.CONVERSIONS.get(x.get("conversion"))
        if conv is None:
            raise ValueError(f"comparison {node}: conversion must be one of {sorted(conv_mod.CONVERSIONS)}")
        if x.get("entity") not in stack.entities:
            raise ValueError(f"comparison {node}: entity {x.get('entity')!r} is unknown")
        market = dict(x.get("market") or {})
        inputs = dict(x.get("inputs") or {})
        if set(market) != set(conv.market_roles):
            raise ValueError(f"comparison {node}: market roles must be {list(conv.market_roles)}")
        if set(inputs) != set(conv.input_roles):
            raise ValueError(f"comparison {node}: input roles must be {list(conv.input_roles)}")
        for role, key in inputs.items():
            if not isinstance(key, str) or key.split(".", 1)[0] not in stack.entities:
                raise ValueError(f"comparison {node}: input {role} {key!r} is not <entity>.<metric>")
        raw_a = x.get("assumptions") or {}
        if set(raw_a) != set(conv.assumption_roles):
            raise ValueError(f"comparison {node}: assumptions must be {list(conv.assumption_roles)}")
        assumptions = {k: (Range.of(v["range"]), str(v.get("note") or "")) for k, v in raw_a.items()}
        rev = x.get("reviewed_at")
        c = Comparison(
            node, x["entity"], x.get("label") or node, conv.id, market, inputs, assumptions,
            date.fromisoformat(str(rev)) if rev else None,
        )  # fmt: skip
        (ok if (rev or drafts) else draft).append(c)
    return ok, draft


def load_forecasts(raw: dict[str, Any], stack: Stack) -> list[Forecast]:
    out: list[Forecast] = []
    ids: set[str] = set()
    for x in raw.get("forecasts") or []:
        fid = x.get("id")
        if not fid or fid in ids:
            raise ValueError(f"forecast id missing or repeated: {fid!r}")
        ids.add(fid)
        key = x.get("key")
        if not isinstance(key, str) or key.split(".", 1)[0] not in stack.entities:
            raise ValueError(f"forecast {fid}: key must be <entity>.<metric> of a known entity")
        p = x["period"]
        start, end = (p, p) if not isinstance(p, list) else (p[0], p[1])
        r = Range.of([x["range"][0], x["value"], x["range"][1]])
        out.append(
            Forecast(
                fid,
                key,
                str(x["unit"]),
                date.fromisoformat(str(start)),
                date.fromisoformat(str(end)),
                r.mid,
                r.lo,
                r.hi,
                date.fromisoformat(str(x["published"])),
                x.get("url"),
                x.get("note"),
            )  # fmt: skip
        )
    return out


def load_files(drafts: bool = False) -> Files:
    graph = load_graph_file()
    node_markets = {k: n.markets for k, n in graph.nodes.items()}
    stack = load_stack(
        _yaml("entities.yaml"), _yaml("sources.yaml"), _yaml("quantities.yaml"), node_markets, drafts_as_verified=drafts
    )
    comps, draft_comps = load_comparisons(_yaml("conversions.yaml"), stack, drafts)
    links, specs = load_scenarios(
        _yaml("scenarios.yaml"), lambda k: isinstance(k, str) and "." in k and k.split(".", 1)[0] in stack.entities
    )
    return Files(
        graph, stack, comps, draft_comps, links, specs, load_forecasts(_yaml("forecasts.yaml"), stack), config_hash()
    )


# ---- database: registry ----------------------------------------------------


def put_sources(conn, sources: list[Source]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """insert into sources (id, url, title, publisher, published, internal) values (%s, %s, %s, %s, %s, %s)
               on conflict (id) do update set url = excluded.url,
                 title = coalesce(excluded.title, sources.title),
                 publisher = coalesce(excluded.publisher, sources.publisher),
                 published = coalesce(excluded.published, sources.published),
                 internal = excluded.internal""",
            [(x.id, x.url, x.title, x.publisher, x.published, x.internal) for x in sources],
        )


def url_source(conn, url: str, title: str | None = None, publisher: str | None = None) -> str:
    """Register a loader's document and return its id."""
    sid = source_id(url)
    put_sources(conn, [Source(sid, url, title, publisher)])
    return sid


def put_entities(conn, stack: Stack) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """insert into entities (id, name, kind, segment, node, markets, ids, note) values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (id) do update set name = excluded.name, kind = excluded.kind, segment = excluded.segment,
                 node = excluded.node, markets = excluded.markets, ids = excluded.ids, note = excluded.note""",
            [
                (e.id, e.name, e.kind, e.segment, e.node, list(e.markets), json.dumps(e.ids), e.note)
                for e in stack.entities.values()
            ],
        )


def register(conn, files: Files) -> None:
    put_sources(conn, list(files.stack.sources.values()))
    put_entities(conn, files.stack)


def sync_relationships(conn, g: Graph) -> int:
    """Version the verified edges: a changed or removed edge closes its row."""
    cur_rows = {r["edge"]: r for r in conn.execute("select * from relationships where valid_to is null")}
    fields = (
        "src",
        "dst",
        "type",
        "size_usd",
        "status",
        "tenor",
        "share",
        "date",
        "source",
        "confidence",
        "verified_at",
    )
    changed = 0
    live = set()
    for e in g.edges:
        live.add(e.id)
        new = (
            e.src,
            e.dst,
            e.type,
            e.size_usd,
            e.status,
            e.tenor,
            e.share,
            e.date,
            e.source,
            e.confidence,
            e.verified_at,
        )
        old = cur_rows.get(e.id)
        if old and tuple(old[f] for f in fields) == new:
            continue
        if old:
            conn.execute("update relationships set valid_to = now() where id = %s", (old["id"],))
        conn.execute(
            f"insert into relationships (edge, {', '.join(fields)}) values (%s, {', '.join(['%s'] * len(fields))})",
            (e.id, *new),
        )
        changed += 1
    for eid, old in cur_rows.items():
        if eid not in live:
            conn.execute("update relationships set valid_to = now() where id = %s", (old["id"],))
            changed += 1
    return changed


# ---- database: quantities --------------------------------------------------


def _row_to_q(r: dict) -> Quantity:
    return Quantity(
        key=r["key"], unit=r["unit"], period_start=r["period_start"], period_end=r["period_end"],
        value=Range(r["lo"], r["value"], r["hi"]), kind=r["kind"], origin=r["origin"], source=r["source"],
        method=r["method"], inputs=tuple(r["inputs"] or ()), verified_at=r["verified_at"],
        revision_reason=r["revision_reason"], note=r["note"], private=r["private"],
    )  # fmt: skip


def current_quantities(conn, origins: tuple[str, ...] | None = None) -> list[Quantity]:
    if origins:
        rows = conn.execute("select * from quantities where valid_to is null and origin = any(%s)", (list(origins),))
    else:
        rows = conn.execute("select * from quantities where valid_to is null")
    return [_row_to_q(r) for r in rows]


def put_quantities(conn, qs: list[Quantity], today: date | None = None, stale_days: int = 90) -> int:
    """Insert new figures and new versions of changed ones; returns how many
    rows were written. Raises on a config revision without a new reason."""
    today = today or date.today()
    written = 0
    for q in qs:
        old = conn.execute(
            """select * from quantities where key = %s and period_start = %s and period_end = %s and valid_to is null""",
            (q.key, q.period_start, q.period_end),
        ).fetchone()
        prev = _row_to_q(old) if old else None
        is_new, reason = revision(prev, q)
        if not is_new:
            if q.origin == "config" and old and old["verified_at"] != q.verified_at:
                conn.execute("update quantities set verified_at = %s where id = %s", (q.verified_at, old["id"]))
            continue
        version = 1
        if old:
            conn.execute("update quantities set valid_to = now() where id = %s", (old["id"],))
            version = old["version"] + 1
        conn.execute(
            """insert into quantities (key, entity, metric, unit, period_start, period_end, value, lo, hi, kind, method,
                                       inputs, source, origin, version, revision_reason, verified_at, stale, note, private)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                q.key, q.entity, q.metric, q.unit, q.period_start, q.period_end, q.value.mid, q.value.lo, q.value.hi,
                q.kind, q.method, list(q.inputs), q.source, q.origin, version, reason, q.verified_at,
                q.stale(today, stale_days), q.note, q.private,
            ),
        )  # fmt: skip
        written += 1
    return written


def close_missing_config(conn, live: list[Quantity]) -> list[str]:
    """Close current config rows whose figure left the file."""
    keep = {(q.key, q.period_start, q.period_end) for q in live}
    gone = []
    for r in conn.execute(
        "select id, key, period_start, period_end from quantities where valid_to is null and origin = 'config'"
    ):
        if (r["key"], r["period_start"], r["period_end"]) not in keep:
            conn.execute("update quantities set valid_to = now() where id = %s", (r["id"],))
            gone.append(f"{r['key']} {r['period_end']}")
    return gone


def flag_stale(conn, today: date, stale_days: int) -> None:
    conn.execute(
        """update quantities set stale = (verified_at is null or verified_at < %s)
           where valid_to is null and origin = 'config'""",
        (today - timedelta(days=stale_days),),
    )


# ---- market inputs ---------------------------------------------------------


def rents(s: Settings, series: Series, day: date) -> list[Quantity]:
    """`<sku>.rent_traded` for `day` from the stored Ornn index (private)."""
    cfg = s.t("stack", "ornn")
    return ornn.rent_ranges(
        series, list(cfg["gpus"]), day, int(cfg["range_days"]), ornn.ornn_source(cfg["source_url"]).id
    )


def closes(conn, coins: list[str], day: date) -> dict[str, tuple[date, float]]:
    """Each market's latest UTC daily close on or before `day`."""
    out = {}
    for r in conn.execute(
        """select distinct on (coin) coin, day, close from candles where coin = any(%s) and day <= %s
           order by coin, day desc""",
        (coins, day),
    ):
        out[r["coin"]] = (r["day"], r["close"])
    return out


# ---- the run ---------------------------------------------------------------


def _src_url(conn, sid: str | None, stack: Stack) -> str | None:
    if not sid:
        return None
    if sid in stack.sources:
        return stack.sources[sid].url
    r = conn.execute("select url from sources where id = %s", (sid,)).fetchone()
    return r["url"] if r else None


def compare(conn, c: Comparison, series: Series, day: date, stack: Stack) -> tuple[dict | None, str | None]:
    """One comparison for one day: (row, None) or (None, why not)."""
    conv = conv_mod.CONVERSIONS[c.conversion]
    got_closes = closes(conn, list(c.market.values()), day)
    inputs: dict[str, Range] = {}
    detail_inputs = []
    for role, coin in c.market.items():
        if coin not in got_closes:
            return None, f"no close for {coin}"
        d, px = got_closes[coin]
        inputs[role] = Range.point(px)
        detail_inputs.append(
            {"name": role, "key": coin, "value": px, "lo": px, "hi": px, "unit": "usd", "kind": "market",
             "source": f"Hyperliquid {coin} UTC close {d}"}
        )  # fmt: skip
    for role, key in c.inputs.items():
        q = series.asof(key, day)
        if q is None:
            return None, f"no figure for {key}"
        inputs[role] = q.value
        detail_inputs.append(
            {"name": role, "key": key, "value": q.value.mid, "lo": q.value.lo, "hi": q.value.hi, "unit": q.unit,
             "kind": q.kind, "period": str(q.period_end), "source": _src_url(conn, q.source, stack),
             "method": q.method, "inputs": list(q.inputs)}
        )  # fmt: skip
    private = any(series.asof(k, day).private for k in c.inputs.values())
    assumptions = {k: r for k, (r, _) in c.assumptions.items()}
    try:
        res = conv_mod.run(conv, inputs, assumptions)
    except ZeroDivisionError as e:
        return None, str(e)
    return {
        "node": c.node,
        "entity": c.entity,
        "label": c.label,
        "unit": conv.unit,
        "conversion": conv.id,
        "private": private,
        "model": res.model,
        "market": res.market,
        "detail": {
            "formula": conv.formula,
            "inputs": detail_inputs,
            "assumptions": [
                {"name": k, "value": r.mid, "lo": r.lo, "hi": r.hi, "unit": "", "note": note}
                for k, (r, note) in c.assumptions.items()
            ],
            "sensitivity": res.sensitivity,
            "model_from": [k for role, k in c.inputs.items() if role in _model_roles(conv)],
            "caveat": "Market prices from Hyperliquid, one venue; not the whole market. Estimates are ranges."
            + (PRIVATE_NOTE if private else ""),
        },
    }, None


def _model_roles(conv: conv_mod.Conversion) -> set[str]:
    probe = {r: Range.point(1.0) for r in (*conv.market_roles, *conv.input_roles)}
    a = {r: Range.point(1.0) for r in conv.assumption_roles}
    used = set()
    for role in conv.input_roles:
        bumped = {**probe, role: Range.point(2.0)}
        if conv.model(bumped, a).mid != conv.model(probe, a).mid:
            used.add(role)
    return used


def run(s: Settings, day: date | None = None, drafts: bool = False, store: bool = True) -> dict:
    """The daily run. With drafts, or without store, nothing is written."""
    day = day or utcnow().date()
    store = store and not drafts
    files = load_files(drafts=drafts)
    stale_days = int(s.t("stack", "stale_days"))
    persist = int(s.t("stack", "gap_persist_days"))
    with connect(s.env.database_url) as conn:
        if store:
            got = conn.execute("select pg_try_advisory_lock(%s) as ok", (MODEL_LOCK,)).fetchone()["ok"]
            conn.commit()
            if not got:
                return {"skipped": "another model run is in progress"}
        try:
            return _run(conn, s, files, day, store, stale_days, persist)
        finally:
            if store:
                conn.execute("select pg_advisory_unlock(%s)", (MODEL_LOCK,))
                conn.commit()


def _run(conn, s: Settings, files: Files, day: date, store: bool, stale_days: int, persist: int) -> dict:
    stack = files.stack
    out: dict[str, Any] = {"day": str(day), "stored": store, "config": files.hash[:12]}
    run_id = None
    if store:
        run_id = conn.execute(
            "insert into model_runs (day, config_hash) values (%s, %s) returning id", (day, files.hash)
        ).fetchone()["id"]
        register(conn, files)
        put_sources(conn, [ornn.ornn_source(s.t("stack", "ornn", "source_url"))])
        out["relationships_changed"] = sync_relationships(conn, files.graph)
        out["config_written"] = put_quantities(conn, stack.quantities, day, stale_days)
        gone = close_missing_config(conn, stack.quantities)
        if gone:
            out["config_closed"] = gone
        flag_stale(conn, day, stale_days)
        base = [q for q in current_quantities(conn) if q.origin != "model"]
        day_rents = rents(s, Series(base), day)
        put_quantities(conn, day_rents, day, stale_days)
        conn.commit()
    else:
        base = [q for q in current_quantities(conn) if q.origin not in ("model", "config")]
        base += stack.quantities
        day_rents = rents(s, Series(base), day)
    base += day_rents
    series = Series(base)
    estimates, missing = evaluate(series, stack.estimates)
    out["quantities"] = len(base)
    out["estimates"] = len(estimates)
    out["missing"] = [_why(m) for m in missing]
    if store:
        out["estimates_written"] = put_quantities(conn, estimates, day, stale_days)

    # Gaps.
    gap_rows = []
    out["comparisons_skipped"] = {}
    for c in files.comparisons:
        row, why = compare(conn, c, series, day, stack)
        if row is None:
            out["comparisons_skipped"][c.node] = why
            continue
        prev = conn.execute(
            "select day, days_apart, flagged from gaps where node = %s and day < %s order by day desc limit 1",
            (c.node, day),
        ).fetchone()
        g = gaps_mod.measure(
            row["model"], row["market"], prev["days_apart"] if prev else 0, persist,
            consecutive=bool(prev and prev["day"] == day - timedelta(days=1)),
        )  # fmt: skip
        row.update(gap=g, newly_flagged=g.flagged and not (prev and prev["flagged"]))
        gap_rows.append(row)
    out["drafts"] = {
        "quantities": len(stack.drafts),
        "judgments": len(stack.draft_estimates),
        "comparisons": [c.node for c in files.draft_comparisons],
    }
    out["gaps"] = [
        {"node": r["node"], "model": [round(x, 6) for x in r["model"].as_list()],
         "market": [round(x, 6) for x in r["market"].as_list()], "unit": r["unit"],
         "distance": round(r["gap"].distance, 4), "overlap": r["gap"].overlap, "days_apart": r["gap"].days_apart,
         "flagged": r["gap"].flagged, "private": r["private"]}
        for r in gap_rows
    ]  # fmt: skip

    # Scenarios.
    names = {e.id: e.name for e in stack.entities.values()}
    scen_rows = []
    for spec in files.scenarios:
        o = propagate(series, files.links, spec, day, names)
        if not o.path:
            out.setdefault("scenarios_skipped", {})[spec.id] = f"no figure for {', '.join(o.missing)}"
            continue
        keys = {st["key"] for st in o.path} | {
            ln.per for ln in files.links if ln.per and ln.src in {st["key"] for st in o.path}
        }
        private = any((q := series.asof(k, day)) is not None and q.private for k in keys)
        scen_rows.append((spec, o, private))
    out["scenarios"] = [
        {"id": sp.id, "steps": len(o.path), "at_risk": o.revenue_at_risk, "private": p} for sp, o, p in scen_rows
    ]

    if store:
        assert run_id is not None
        with conn.cursor() as cur:
            cur.execute("delete from gaps where day = %s", (day,))
            for r in gap_rows:
                g = r["gap"]
                cur.execute(
                    """insert into gaps (day, node, run_id, entity, label, metric, unit, model_value, model_lo, model_hi,
                                         market_value, market_lo, market_hi, distance, overlap, days_apart, flagged,
                                         conversion, detail, private)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        day, r["node"], run_id, r["entity"], r["label"], r["node"].split(".", 1)[1], r["unit"],
                        r["model"].mid, r["model"].lo, r["model"].hi, r["market"].mid, r["market"].lo, r["market"].hi,
                        g.distance, g.overlap, g.days_apart, g.flagged, r["conversion"], json.dumps(r["detail"]),
                        r["private"],
                    ),
                )  # fmt: skip
            cur.execute("delete from scenarios where day = %s", (day,))
            for spec, o, private in scen_rows:
                cur.execute(
                    """insert into scenarios (id, day, run_id, label, shock, path, revenue_at_risk, private)
                       values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (spec.id, day, run_id, spec.label,
                     json.dumps({"key": spec.key, "change": spec.change, "note": spec.note}),
                     json.dumps(o.path), json.dumps(o.revenue_at_risk), private),
                )  # fmt: skip
        out["forecasts"] = sync_forecasts(conn, files.forecasts)
        conn.execute(
            """insert into definitions (key, value, updated_at) values ('stack', %s, now())
               on conflict (key) do update set value = excluded.value, updated_at = now()""",
            (json.dumps({"stale_days": stale_days, "gap_persist_days": persist}),),
        )
        flags = [r for r in gap_rows if r["gap"].flagged]
        conn.execute(
            """update model_runs set finished_at = now(), ok = true, quantities = %s, estimates = %s, gaps = %s,
                                     flags = %s, note = %s where id = %s""",
            (len(base), len(estimates), len(gap_rows), len(flags), _note(out), run_id),
        )
        conn.commit()
        fresh = [r for r in gap_rows if r["newly_flagged"]]
        if fresh:
            Telegram(s.env).message(
                "Stack Engine: new persistent gaps\n"
                + "\n".join(
                    f"{'[PRIVATE, not for publication] ' if r['private'] else ''}"
                    f"{r['label']}: market {r['market'].mid:,.4g} vs model {r['model'].mid:,.4g} {r['unit']} "
                    f"({r['gap'].distance * 100:+.0f}%, {r['gap'].days_apart} days apart)"
                    for r in fresh
                )
                + (f"\n{s.env.site_url}/gaps" if any(not r["private"] for r in fresh) else "")
            )
    return out


def _why(m: Missing) -> str:
    return f"{m.key}{f' {m.period_end}' if m.period_end else ''}: needs {', '.join(m.inputs)}"


def _note(out: dict) -> str:
    parts = []
    if out.get("comparisons_skipped"):
        parts.append("skipped " + "; ".join(f"{k}: {v}" for k, v in out["comparisons_skipped"].items()))
    if out.get("missing"):
        parts.append(f"{len(out['missing'])} estimates lack inputs")
    return ". ".join(parts)[:1000] or "ok"


def sync_forecasts(conn, fs: list[Forecast]) -> dict:
    """Insert new forecasts (published ones are never edited) and score every
    unscored one whose actual figure is in: a current, non-estimate figure
    for the same key and period."""
    added = 0
    for f in fs:
        old = conn.execute("select * from forecasts where id = %s", (f.id,)).fetchone()
        if old is None:
            conn.execute(
                """insert into forecasts (id, key, entity, metric, unit, period_start, period_end, value, lo, hi,
                                          published, url, note)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (f.id, f.key, f.key.split(".", 1)[0], f.key.split(".", 1)[1], f.unit, f.period_start, f.period_end,
                 f.value, f.lo, f.hi, f.published, f.url, f.note),
            )  # fmt: skip
            added += 1
        elif (old["value"], old["lo"], old["hi"], old["period_end"], old["published"]) != (
            f.value, f.lo, f.hi, f.period_end, f.published,
        ):  # fmt: skip
            raise ValueError(f"forecast {f.id} changed after publication; publish a new forecast instead")
    scored = 0
    for r in conn.execute(
        """select f.id, f.value, f.lo, f.hi, q.value as actual, q.source, q.kind from forecasts f
           join quantities q on q.key = f.key and q.period_start = f.period_start and q.period_end = f.period_end
                and q.valid_to is null and q.kind <> 'estimate' and not q.private
           where f.scored_at is null"""
    ).fetchall():
        sc = ledger.score(r["value"], r["lo"], r["hi"], r["actual"])
        conn.execute(
            """update forecasts set actual = %s, actual_source = %s, actual_kind = %s, error = %s, within = %s,
                                    scored_at = now() where id = %s""",
            (r["actual"], r["source"], r["kind"], sc.error, sc.within, r["id"]),
        )
        scored += 1
    return {"added": added, "scored": scored}


def daily_job(s: Settings, force: bool = False) -> dict:
    """Vercel cron entry: once per UTC day, after the 00:00 UTC closes."""
    day = utcnow().date()
    with connect(s.env.database_url) as conn:
        done = conn.execute("select 1 from model_runs where day = %s and ok", (day,)).fetchone()
    if done and not force:
        return {"skipped": f"model for {day} already ran"}
    return run(s, day)


def check(drafts_list: bool = False) -> list[str]:
    """`tape stack`: what loads, what is a draft, what is unused."""
    f = load_files()
    st = f.stack
    lines = [
        f"{len(st.entities)} entities, {len(st.quantities)} verified figures, {len(st.drafts)} draft figures, "
        f"{len(st.estimates)} estimates ({len(st.draft_estimates)} draft judgments), "
        f"{len(f.comparisons)} reviewed comparisons ({len(f.draft_comparisons)} drafts), "
        f"{len(f.scenarios)} scenarios, {len(f.forecasts)} forecasts",
    ]
    if drafts_list:
        for q in st.drafts:
            lines.append(
                f"  draft figure    {q.key} {q.period_end} {q.value.mid:g} {q.unit} ({q.kind}) {st.sources[q.source].url}"
            )
        for e in st.draft_estimates:
            assert e.value
            lines.append(f"  draft judgment  {e.key} {e.value.as_list()} {e.unit}: {e.basis}")
        for c in f.draft_comparisons:
            lines.append(
                f"  draft comparison {c.node}: assumptions "
                + ", ".join(f"{k} {r.as_list()}" for k, (r, _) in c.assumptions.items())
            )
    return lines
