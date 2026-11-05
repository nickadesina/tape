"""Ranges, entities, quantities, and the stack config.

Every number in the model is a Range (low, middle, high): estimates are never
single points. A filed figure is a Range whose three values are equal.

config/stack/ holds the hand-checked part of the model, versioned in the repo
like config/graph.yaml:

  entities.yaml     companies, facilities, SKUs and themes
  sources.yaml      documents cited by key (a quantity may also cite a URL)
  quantities.yaml   figures a person entered from a source (`quantities`) and
                    estimates the model computes from other figures (`estimates`)

A figure without `verified_at` is a draft: it is not loaded, and `tape stack`
lists it. Loaders (EDGAR, Taiwan, Korea, SF Compute) add filed and reported
figures straight from their source, and the model run computes the estimates.

Rules for estimates (design doc, "Rules for estimates"):
  - every estimate states its method and points to its inputs;
  - ranges, never single points;
  - every revision is logged with its reason;
  - estimates are always labelled as estimates.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

KINDS = {"filed", "company", "reported", "estimate"}
ENTITY_KINDS = {"company", "facility", "sku", "theme"}
SEGMENTS = ("demand", "accelerators", "memory", "packaging", "power", "cloud", "economics", "financing")
# Estimate methods. Each is a documented formula over the input Ranges, in the
# order the estimate lists them.
METHODS = {
    "sum": "the sum of the inputs",
    "product": "the product of the inputs",
    "ratio": "the first input divided by the second",
    "difference": "the first input minus the second",
    "min": "the smallest input: the binding constraint",
    "judgment": "a stated range from the cited basis, with no inputs",
}


# ---- ranges ----------------------------------------------------------------


@dataclass(frozen=True)
class Range:
    lo: float
    mid: float
    hi: float

    def __post_init__(self) -> None:
        if not (self.lo <= self.mid <= self.hi):
            raise ValueError(f"range out of order: {self.lo} <= {self.mid} <= {self.hi}")

    @staticmethod
    def point(x: float) -> Range:
        return Range(x, x, x)

    @staticmethod
    def of(x: Any) -> Range:
        """A number, [lo, hi], [lo, mid, hi] or a Range."""
        if isinstance(x, Range):
            return x
        if isinstance(x, int | float):
            return Range.point(float(x))
        xs = [float(v) for v in x]
        if len(xs) == 2:
            return Range(xs[0], (xs[0] + xs[1]) / 2, xs[1])
        if len(xs) == 3:
            return Range(*xs)
        raise ValueError(f"not a range: {x!r}")

    def as_list(self) -> list[float]:
        return [self.lo, self.mid, self.hi]

    def overlaps(self, o: Range) -> bool:
        return self.lo <= o.hi and o.lo <= self.hi

    def scale(self, k: float) -> Range:
        a, b = self.lo * k, self.hi * k
        return Range(min(a, b), self.mid * k, max(a, b))

    @staticmethod
    def _corners(lo: Sequence[float], mid: float) -> Range:
        a, b = min(lo), max(lo)
        return Range(a, min(max(mid, a), b), b)

    def __add__(self, o: Range) -> Range:
        return Range(self.lo + o.lo, self.mid + o.mid, self.hi + o.hi)

    def __sub__(self, o: Range) -> Range:
        return Range(self.lo - o.hi, self.mid - o.mid, self.hi - o.lo)

    def __mul__(self, o: Range) -> Range:
        c = (self.lo * o.lo, self.lo * o.hi, self.hi * o.lo, self.hi * o.hi)
        return Range._corners(c, self.mid * o.mid)

    def __truediv__(self, o: Range) -> Range:
        if o.lo <= 0 <= o.hi:
            raise ZeroDivisionError("dividing by a range that contains zero")
        c = (self.lo / o.lo, self.lo / o.hi, self.hi / o.lo, self.hi / o.hi)
        return Range._corners(c, self.mid / o.mid)

    def min(self, o: Range) -> Range:
        return Range(min(self.lo, o.lo), min(self.mid, o.mid), min(self.hi, o.hi))


def change(base: Range, new: Range) -> Range:
    """Relative change from base to new, as a Range (pairs lo with lo)."""
    vals = sorted(((new.lo - base.lo) / base.lo, (new.hi - base.hi) / base.hi)) if base.lo and base.hi else [0.0, 0.0]
    m = (new.mid - base.mid) / base.mid if base.mid else 0.0
    return Range(min(vals[0], m), m, max(vals[1], m))


# ---- records ---------------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    kind: str
    segment: str
    node: str | None = None
    markets: tuple[str, ...] = ()
    ids: dict[str, str] = field(default_factory=dict)
    note: str | None = None


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    title: str | None = None
    publisher: str | None = None
    published: date | None = None


def source_id(url: str) -> str:
    return "src_" + hashlib.sha1(url.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Quantity:
    key: str  # <entity>.<metric>
    unit: str
    period_start: date
    period_end: date
    value: Range
    kind: str
    origin: str  # config | edgar | taiwan | korea | sfcompute | model
    source: str | None = None  # a sources id; None only for estimates
    method: str | None = None
    inputs: tuple[str, ...] = ()
    verified_at: date | None = None
    revision_reason: str | None = None
    note: str | None = None

    @property
    def entity(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def metric(self) -> str:
        return self.key.split(".", 1)[1]

    def same_figure(self, o: Quantity) -> bool:
        """Whether two versions say the same thing (a revision is any difference)."""
        return (
            self.unit == o.unit
            and self.kind == o.kind
            and self.source == o.source
            and all(
                math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
                for a, b in zip(self.value.as_list(), o.value.as_list(), strict=True)
            )
        )

    def stale(self, today: date, stale_days: int) -> bool:
        return self.origin == "config" and (
            self.verified_at is None or today - self.verified_at > timedelta(days=stale_days)
        )


@dataclass(frozen=True)
class EstimateSpec:
    key: str
    unit: str
    method: str
    inputs: tuple[str, ...] = ()
    period: tuple[date, date] | None = None  # None: one estimate per period of the first input
    value: Range | None = None  # judgment only
    basis: str | None = None  # judgment only: what the range rests on
    source: str | None = None  # judgment only
    note: str | None = None


@dataclass
class Stack:
    entities: dict[str, Entity]
    sources: dict[str, Source]
    quantities: list[Quantity]  # verified config figures
    estimates: list[EstimateSpec]
    drafts: list[Quantity] = field(default_factory=list)
    draft_estimates: list[EstimateSpec] = field(default_factory=list)

    def entity_of(self, key: str) -> Entity:
        return self.entities[key.split(".", 1)[0]]


# ---- loading ---------------------------------------------------------------


def _date(x: Any, what: str) -> date:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x))
    except ValueError as err:
        raise ValueError(f"{what}: {x!r} is not a date") from err


def _period(x: Any, what: str) -> tuple[date, date]:
    if isinstance(x, list | tuple):
        if len(x) != 2:
            raise ValueError(f"{what}: a period is a date or [start, end]")
        a, b = _date(x[0], what), _date(x[1], what)
    else:
        a = b = _date(x, what)
    if b < a:
        raise ValueError(f"{what}: period ends before it starts")
    return a, b


def _key(key: Any, entities: dict[str, Entity], what: str) -> str:
    if not isinstance(key, str) or "." not in key:
        raise ValueError(f"{what}: key must be <entity>.<metric>, got {key!r}")
    if key.split(".", 1)[0] not in entities:
        raise ValueError(f"{what}: {key.split('.', 1)[0]!r} is not an entity")
    return key


def load_stack(
    raw_entities: dict[str, Any],
    raw_sources: dict[str, Any],
    raw_quantities: dict[str, Any],
    node_markets: dict[str, tuple[str, ...]] | None = None,
    drafts_as_verified: bool = False,
) -> Stack:
    """Validate and build the stack config. Raises ValueError on anything
    malformed: a wrong number drives a wrong claim, so nothing half-loads."""
    node_markets = node_markets or {}
    entities: dict[str, Entity] = {}
    for eid, e in (raw_entities.get("entities") or {}).items():
        if "." in eid:
            raise ValueError(f"entity {eid}: ids cannot contain a dot")
        if e.get("kind") not in ENTITY_KINDS:
            raise ValueError(f"entity {eid}: kind must be one of {sorted(ENTITY_KINDS)}")
        if e.get("segment") not in SEGMENTS:
            raise ValueError(f"entity {eid}: segment must be one of {list(SEGMENTS)}")
        node = e.get("node")
        if node is not None and node_markets and node not in node_markets:
            raise ValueError(f"entity {eid}: node {node!r} is not in config/graph.yaml")
        entities[eid] = Entity(
            id=eid,
            name=e["name"],
            kind=e["kind"],
            segment=e["segment"],
            node=node,
            markets=tuple(node_markets.get(node, ())) if node else (),
            ids={k: str(v) for k, v in (e.get("ids") or {}).items()},
            note=e.get("note"),
        )

    sources: dict[str, Source] = {}
    for sid, x in (raw_sources.get("sources") or {}).items():
        if not str(x.get("url") or "").startswith("http"):
            raise ValueError(f"source {sid}: needs a url")
        sources[sid] = Source(
            sid,
            x["url"],
            x.get("title"),
            x.get("publisher"),
            _date(x["published"], sid) if x.get("published") else None,
        )

    def cite(ref: Any, what: str) -> str:
        if ref in sources:
            return ref
        if isinstance(ref, str) and ref.startswith("http"):
            sid = source_id(ref)
            sources.setdefault(sid, Source(sid, ref))
            return sid
        raise ValueError(f"{what}: source must be a key in sources.yaml or a URL")

    quantities: list[Quantity] = []
    drafts: list[Quantity] = []
    seen: set[tuple[str, date, date]] = set()
    for i, q in enumerate(raw_quantities.get("quantities") or []):
        what = f"quantity {q.get('key') or i}"
        key = _key(q.get("key"), entities, what)
        kind = q.get("kind")
        if kind not in KINDS - {"estimate"}:
            raise ValueError(f"{what}: kind must be filed, company or reported (estimates go under `estimates`)")
        if not q.get("unit"):
            raise ValueError(f"{what}: needs a unit")
        start, end = _period(q.get("period"), what)
        if (key, start, end) in seen:
            raise ValueError(f"{what}: the same period twice")
        seen.add((key, start, end))
        if "value" not in q:
            raise ValueError(f"{what}: needs a value")
        v = float(q["value"])
        rng = Range.of(q["range"]) if q.get("range") is not None else Range.point(v)
        if not (rng.lo <= v <= rng.hi):
            raise ValueError(f"{what}: value outside its range")
        rng = Range(rng.lo, v, rng.hi)
        verified = q.get("verified_at")
        qty = Quantity(
            key=key,
            unit=str(q["unit"]),
            period_start=start,
            period_end=end,
            value=rng,
            kind=kind,
            origin="config",
            source=cite(q.get("source"), what),
            verified_at=_date(verified, what) if verified else None,
            revision_reason=q.get("revision_reason"),
            note=q.get("note"),
        )
        if verified or drafts_as_verified:
            quantities.append(qty if verified else replace(qty, verified_at=date.today()))
        else:
            drafts.append(qty)

    estimates: list[EstimateSpec] = []
    draft_estimates: list[EstimateSpec] = []
    keys: set[str] = set()
    for i, x in enumerate(raw_quantities.get("estimates") or []):
        what = f"estimate {x.get('key') or i}"
        key = _key(x.get("key"), entities, what)
        if key in keys:
            raise ValueError(f"{what}: defined twice")
        keys.add(key)
        method = x.get("method")
        if method not in METHODS:
            raise ValueError(f"{what}: method must be one of {sorted(METHODS)}")
        if not x.get("unit"):
            raise ValueError(f"{what}: needs a unit")
        inputs = tuple(_key(k, entities, what) for k in x.get("inputs") or ())
        period = _period(x["period"], what) if x.get("period") is not None else None
        value = basis = src = None
        if method == "judgment":
            if inputs:
                raise ValueError(f"{what}: a judgment has no inputs")
            if x.get("range") is None or not x.get("basis") or not x.get("source") or period is None:
                raise ValueError(f"{what}: a judgment needs a range, a period, a basis and a source")
            value = Range.of(x["range"])
            if value.lo == value.hi:
                raise ValueError(f"{what}: an estimate is a range, never a single point")
            basis, src = x["basis"], cite(x["source"], what)
            if not x.get("verified_at") and not drafts_as_verified:
                # A judgment is a figure a person stands behind, like a config quantity.
                draft_estimates.append(
                    EstimateSpec(key, str(x["unit"]), method, (), period, value, basis, src, x.get("note"))
                )
                continue
        else:
            need = {"ratio": 2, "difference": 2}.get(method)
            if not inputs or (need and len(inputs) != need):
                raise ValueError(f"{what}: {method} needs {need or 'at least one'} input(s)")
            if key in inputs:
                raise ValueError(f"{what}: an estimate cannot be its own input")
        estimates.append(EstimateSpec(key, str(x["unit"]), method, inputs, period, value, basis, src, x.get("note")))
    _check_acyclic(estimates)
    return Stack(entities, sources, quantities, estimates, drafts, draft_estimates)


def _check_acyclic(specs: Sequence[EstimateSpec]) -> None:
    order_estimates(specs)


def order_estimates(specs: Sequence[EstimateSpec]) -> list[EstimateSpec]:
    """Estimates in dependency order. Raises on a cycle."""
    by_key = {s.key: s for s in specs}
    out: list[EstimateSpec] = []
    state: dict[str, int] = {}

    def visit(k: str, chain: tuple[str, ...]) -> None:
        if state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise ValueError(f"estimates form a cycle: {' -> '.join((*chain, k))}")
        state[k] = 1
        for i in by_key[k].inputs:
            if i in by_key:
                visit(i, (*chain, k))
        state[k] = 2
        out.append(by_key[k])

    for s in specs:
        visit(s.key, ())
    return out


# ---- evaluation ------------------------------------------------------------


class Series:
    """Current quantities by key, each key's figures sorted by period end."""

    def __init__(self, qs: Iterable[Quantity] = ()):
        self.by_key: dict[str, list[Quantity]] = {}
        for q in qs:
            self.add(q)

    def add(self, q: Quantity) -> None:
        xs = self.by_key.setdefault(q.key, [])
        xs[:] = [x for x in xs if (x.period_start, x.period_end) != (q.period_start, q.period_end)]
        xs.append(q)
        xs.sort(key=lambda x: (x.period_end, x.period_start))

    def asof(self, key: str, end: date) -> Quantity | None:
        """The figure for `key` with the latest period end on or before `end`."""
        best = None
        for q in self.by_key.get(key, []):
            if q.period_end <= end:
                best = q
        return best

    def latest(self, key: str) -> Quantity | None:
        xs = self.by_key.get(key)
        return xs[-1] if xs else None

    def all(self) -> list[Quantity]:
        return [q for xs in self.by_key.values() for q in xs]


def apply_method(method: str, xs: Sequence[Range]) -> Range:
    if method == "sum":
        out = xs[0]
        for x in xs[1:]:
            out = out + x
        return out
    if method == "product":
        out = xs[0]
        for x in xs[1:]:
            out = out * x
        return out
    if method == "ratio":
        return xs[0] / xs[1]
    if method == "difference":
        return xs[0] - xs[1]
    if method == "min":
        out = xs[0]
        for x in xs[1:]:
            out = out.min(x)
        return out
    raise ValueError(f"no formula for method {method!r}")


@dataclass(frozen=True)
class Missing:
    key: str
    period_end: date | None
    inputs: tuple[str, ...]


def evaluate(series: Series, specs: Sequence[EstimateSpec]) -> tuple[list[Quantity], list[Missing]]:
    """Compute every estimate it can, in dependency order, adding each to
    `series` so later estimates can read it. Returns the estimates and the
    ones it could not compute for lack of inputs."""
    out: list[Quantity] = []
    missing: list[Missing] = []
    for spec in order_estimates(specs):
        if spec.method == "judgment":
            assert spec.period and spec.value
            q = Quantity(
                spec.key, spec.unit, spec.period[0], spec.period[1], spec.value, "estimate", "model",
                source=spec.source, method="judgment", note=spec.basis,
            )  # fmt: skip
            series.add(q)
            out.append(q)
            continue
        if spec.period:
            periods = [spec.period]
        else:
            periods = [(q.period_start, q.period_end) for q in series.by_key.get(spec.inputs[0], [])]
            if not periods:
                missing.append(Missing(spec.key, None, spec.inputs))
                continue
        for start, end in periods:
            got = [series.asof(k, end) for k in spec.inputs]
            absent = tuple(k for k, g in zip(spec.inputs, got, strict=True) if g is None)
            if absent:
                missing.append(Missing(spec.key, end, absent))
                continue
            try:
                v = apply_method(spec.method, [g.value for g in got if g])
            except (ZeroDivisionError, ValueError):
                missing.append(Missing(spec.key, end, spec.inputs))
                continue
            q = Quantity(
                spec.key, spec.unit, start, end, v, "estimate", "model",
                method=spec.method, inputs=spec.inputs, note=spec.note,
            )  # fmt: skip
            series.add(q)
            out.append(q)
    return out, missing


def revision(prev: Quantity | None, new: Quantity) -> tuple[bool, str | None]:
    """Whether `new` is a new version of `prev`, and why. A config figure
    needs a stated reason that differs from the last version's; estimates and
    loader figures say why on their own."""
    if prev is None:
        return True, None
    if prev.same_figure(new):
        return False, None
    if new.origin == "config":
        if not new.revision_reason or new.revision_reason == prev.revision_reason:
            raise ValueError(
                f"{new.key} {new.period_end}: changed from {prev.value.mid:g} to {new.value.mid:g} without a new "
                "revision_reason (every revision is logged with its reason)"
            )
        return True, new.revision_reason
    if new.origin == "model":
        return True, f"recomputed from {', '.join(new.inputs) or 'its basis'}"
    return True, new.revision_reason or f"{new.origin} figure changed"
