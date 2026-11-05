"""Scenarios: change one input and push it through the quantity graph along
explicit, documented links (design doc, layer 3). For example, an HBM
shortfall caps accelerator units, which caps cloud capacity, which moves rents.

Links (config/stack/scenarios.yaml), each with its basis:

  elasticity  %change(dst) = elasticity x %change(src), elasticity a Range
  cap         dst <= src / per, `per` a quantity (e.g. GB of HBM per unit):
              dst falls only when the shocked supply binds

The output is the path, with base and shocked ranges at every step, and
revenue at risk by company: every quantity on the path whose metric is a
revenue, base minus shocked. Citrini's "who wins and who loses", computed
instead of argued.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Any

from .core import Range, Series, change

LINK_KINDS = {"elasticity", "cap"}


@dataclass(frozen=True)
class Link:
    src: str
    dst: str
    kind: str
    basis: str  # what the link rests on; printed on the exhibit
    elasticity: Range | None = None
    per: str | None = None


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    label: str
    key: str
    change: float  # e.g. -0.10
    note: str | None = None


def load_scenarios(raw: dict[str, Any], keys_ok) -> tuple[list[Link], list[ScenarioSpec]]:
    """`keys_ok(key)` says whether a key names a known entity."""
    links: list[Link] = []
    for i, x in enumerate(raw.get("links") or []):
        what = f"link {i} ({x.get('from')} -> {x.get('to')})"
        if x.get("kind") not in LINK_KINDS:
            raise ValueError(f"{what}: kind must be one of {sorted(LINK_KINDS)}")
        for k in ("from", "to"):
            if not keys_ok(x.get(k)):
                raise ValueError(f"{what}: {k} is not <entity>.<metric> of a known entity")
        if not x.get("basis"):
            raise ValueError(f"{what}: every link states its basis")
        if x["kind"] == "elasticity" and x.get("elasticity") is None:
            raise ValueError(f"{what}: an elasticity link needs an elasticity range")
        if x["kind"] == "cap" and not keys_ok(x.get("per")):
            raise ValueError(f"{what}: a cap link needs `per`, a quantity key")
        links.append(
            Link(
                x["from"],
                x["to"],
                x["kind"],
                x["basis"],
                Range.of(x["elasticity"]) if x.get("elasticity") is not None else None,
                x.get("per"),
            )  # fmt: skip
        )
    specs: list[ScenarioSpec] = []
    ids: set[str] = set()
    for x in raw.get("scenarios") or []:
        sid = x.get("id")
        if not sid or sid in ids:
            raise ValueError(f"scenario id missing or repeated: {sid!r}")
        ids.add(sid)
        if not keys_ok(x.get("shock", {}).get("key")):
            raise ValueError(f"scenario {sid}: shock.key is not a known quantity key")
        specs.append(ScenarioSpec(sid, x["label"], x["shock"]["key"], float(x["shock"]["change"]), x.get("note")))
    return links, specs


def _pairwise_diff(a: Range, b: Range) -> Range:
    xs = sorted((a.lo - b.lo, a.hi - b.hi))
    m = a.mid - b.mid
    return Range(min(xs[0], m), m, max(xs[1], m))


@dataclass(frozen=True)
class Outcome:
    path: list[dict]
    revenue_at_risk: list[dict]
    missing: list[str]


def propagate(series: Series, links: list[Link], spec: ScenarioSpec, asof: date, names: dict[str, str]) -> Outcome:
    def base(k: str) -> Range | None:
        q = series.asof(k, asof)
        return q.value if q else None

    units = {k: (series.latest(k).unit if series.latest(k) else "") for k in series.by_key}
    b0 = base(spec.key)
    if b0 is None:
        return Outcome([], [], [spec.key])
    shocked: dict[str, Range] = {spec.key: b0.scale(1 + spec.change)}
    path = [
        {"key": spec.key, "label": spec.key, "unit": units.get(spec.key, ""), "base": b0.as_list(),
         "shocked": shocked[spec.key].as_list(), "via": f"shock {spec.change * 100:+.0f}%"}
    ]  # fmt: skip
    missing: list[str] = []
    out_links: dict[str, list[Link]] = {}
    for ln in links:
        out_links.setdefault(ln.src, []).append(ln)
    q = deque([spec.key])
    done: set[tuple[str, str]] = set()
    while q:
        k = q.popleft()
        for ln in out_links.get(k, []):
            if (ln.src, ln.dst) in done:
                continue
            done.add((ln.src, ln.dst))
            bs, bd = base(ln.src), base(ln.dst)
            if bs is None or bd is None:
                missing.append(ln.dst if bd is None else ln.src)
                continue
            cur = shocked.get(ln.dst, bd)
            if ln.kind == "elasticity":
                assert ln.elasticity
                pct = change(bs, shocked[ln.src]) * ln.elasticity
                new = cur * (Range.point(1.0) + pct)
            else:
                per = base(ln.per) if ln.per else None
                if per is None:
                    missing.append(ln.per or "")
                    continue
                new = cur.min(shocked[ln.src] / per)
            shocked[ln.dst] = new
            path.append(
                {"key": ln.dst, "label": ln.dst, "unit": units.get(ln.dst, ""), "base": bd.as_list(),
                 "shocked": new.as_list(), "via": f"{ln.kind} from {ln.src}: {ln.basis}"}
            )  # fmt: skip
            q.append(ln.dst)
    risk = []
    for step in path[1:]:
        k = step["key"]
        if "revenue" in k.split(".", 1)[1] and step["unit"].startswith("usd"):
            d = _pairwise_diff(Range(*step["base"]), Range(*step["shocked"]))
            ent = k.split(".", 1)[0]
            risk.append({"entity": ent, "name": names.get(ent, ent), "lo": d.lo, "mid": d.mid, "hi": d.hi,
                         "via": step["via"]})  # fmt: skip
    risk.sort(key=lambda r: -r["mid"])
    return Outcome(path, risk, sorted(set(missing)))
