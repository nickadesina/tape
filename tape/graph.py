"""The financing graph: who is tied to whom, how, and on what source.

config/graph.yaml is hand-checked. An agent drafts edges from filings and
reporting; a person verifies each one (sets `verified_at`) before it ships.
Only verified edges are loaded; drafts stay in the file and `tape graph`
lists them. Edges whose last check is older than `stale_days` are flagged
stale on the site.

Direction follows the money: `from` pays, invests, lends or buys; `to`
receives. Pure apart from `load_graph_file` and `write_graph`.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import yaml

from .settings import CONFIG_DIR

NODE_KINDS = {"lab", "neocloud", "chip", "memory", "optics", "hyperscaler", "lender", "fund", "other"}
EDGE_TYPES = {"equity", "compute_contract", "supply", "debt_lease", "revenue_concentration", "guarantee"}
CONFIDENCE = {"filed", "company", "reported"}
STATUS = {"announced", "contracted", None}
# Money out of a node along these edges makes the receiver depend on it: a
# lab's compute contracts are its neocloud's revenue, a neocloud's chip orders
# are its supplier's.
DEPENDENCE = {"compute_contract", "supply", "revenue_concentration"}


@dataclass(frozen=True)
class Node:
    key: str
    name: str
    kind: str
    public: bool
    markets: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class Edge:
    id: str
    src: str
    dst: str
    type: str
    date: date
    source: str
    confidence: str
    size_usd: float | None = None
    status: str | None = None
    tenor: str | None = None
    share: float | None = None
    note: str | None = None
    verified_at: date | None = None

    def stale(self, today: date, stale_days: int) -> bool:
        return self.verified_at is None or today - self.verified_at > timedelta(days=stale_days)


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]  # verified only
    drafts: list[Edge] = field(default_factory=list)
    corrections: list[dict[str, Any]] = field(default_factory=list)

    def node_of_market(self) -> dict[str, str]:
        return {m: n.key for n in self.nodes.values() for m in n.markets}

    def tradable(self, key: str) -> bool:
        return bool(self.nodes[key].markets)

    def out_edges(self) -> dict[str, list[Edge]]:
        out: dict[str, list[Edge]] = defaultdict(list)
        for e in self.edges:
            out[e.src].append(e)
        return out

    def dependents(self, key: str, depth: int = 2) -> dict[str, int]:
        """Nodes whose revenue runs through `key`, with their hop count: one hop
        is a direct customer relationship, two is a supplier of one."""
        out_e = self.out_edges()
        seen = {key: 0}
        q = deque([key])
        while q:
            k = q.popleft()
            if seen[k] >= depth:
                continue
            for e in out_e.get(k, []):
                if e.type in DEPENDENCE and e.dst not in seen:
                    seen[e.dst] = seen[k] + 1
                    q.append(e.dst)
        seen.pop(key)
        return seen

    def circular(self, e: Edge, max_len: int = 3) -> bool:
        """True when money leaving `e.dst` comes back to `e.src` within
        `max_len` edges in all: the deal is part of a loop."""
        out_e = self.out_edges()
        frontier = {e.dst}
        for _ in range(max_len - 1):
            nxt = set()
            for k in frontier:
                for f in out_e.get(k, []):
                    if f.dst == e.src:
                        return True
                    nxt.add(f.dst)
            frontier = nxt
        return False

    def paths(self, a: str, b: str, max_len: int = 3) -> list[list[Edge]]:
        """Every simple path between two nodes up to `max_len` edges, either direction."""
        adj: dict[str, list[tuple[str, Edge]]] = defaultdict(list)
        for e in self.edges:
            adj[e.src].append((e.dst, e))
            adj[e.dst].append((e.src, e))
        out: list[list[Edge]] = []

        def go(k: str, path: list[Edge], seen: set[str]) -> None:
            if k == b and path:
                out.append(list(path))
                return
            if len(path) >= max_len:
                return
            for n, e in adj[k]:
                if n not in seen:
                    seen.add(n)
                    path.append(e)
                    go(n, path, seen)
                    path.pop()
                    seen.discard(n)

        go(a, [], {a})
        return out

    def exposures(self) -> dict[str, dict[str, Any]]:
        """Per node: the share of revenue from its largest counterparty, and the
        commitments it has made (money out on compute and supply edges)."""
        out: dict[str, dict[str, Any]] = {k: {} for k in self.nodes}
        for e in self.edges:
            if e.type == "revenue_concentration" and e.share is not None:
                cur = out[e.dst].get("top_customer_share")
                if cur is None or e.share > cur:
                    out[e.dst]["top_customer_share"] = e.share
                    out[e.dst]["top_customer"] = e.src
            if e.type in ("compute_contract", "supply") and e.size_usd:
                k = "commitments_contracted_usd" if e.status == "contracted" else "commitments_announced_usd"
                out[e.src][k] = out[e.src].get(k, 0.0) + e.size_usd
        return out


def _date(x: Any, what: str) -> date:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x))
    except ValueError as err:
        raise ValueError(f"{what}: {x!r} is not a date") from err


def load_graph(raw: dict[str, Any]) -> Graph:
    """Validate and build the graph. Raises ValueError on any malformed node
    or edge: a wrong edge drives a wrong claim, so nothing half-loads."""
    nodes: dict[str, Node] = {}
    for key, n in (raw.get("nodes") or {}).items():
        if n.get("kind") not in NODE_KINDS:
            raise ValueError(f"node {key}: kind must be one of {sorted(NODE_KINDS)}")
        nodes[key] = Node(
            key=key,
            name=n["name"],
            kind=n["kind"],
            public=bool(n.get("public")),
            markets=tuple(n.get("markets") or ()),
            note=n.get("note"),
        )
    seen_m: dict[str, str] = {}
    for n in nodes.values():
        for m in n.markets:
            if m in seen_m:
                raise ValueError(f"{m} is on two nodes: {seen_m[m]} and {n.key}")
            seen_m[m] = n.key
    edges: list[Edge] = []
    drafts: list[Edge] = []
    ids: set[str] = set()
    for e in raw.get("edges") or []:
        eid = e.get("id")
        if not eid or eid in ids:
            raise ValueError(f"edge id missing or repeated: {eid!r}")
        ids.add(eid)
        for end in ("from", "to"):
            if e.get(end) not in nodes:
                raise ValueError(f"edge {eid}: {end} {e.get(end)!r} is not a node")
        if e.get("type") not in EDGE_TYPES:
            raise ValueError(f"edge {eid}: type must be one of {sorted(EDGE_TYPES)}")
        if e.get("confidence") not in CONFIDENCE:
            raise ValueError(f"edge {eid}: confidence must be one of {sorted(CONFIDENCE)}")
        if e.get("status") not in STATUS:
            raise ValueError(f"edge {eid}: status must be announced or contracted")
        if not str(e.get("source") or "").startswith("http"):
            raise ValueError(f"edge {eid}: every edge needs a linked source")
        share = e.get("share")
        if e["type"] == "revenue_concentration" and share is None:
            raise ValueError(f"edge {eid}: revenue_concentration needs a share")
        edge = Edge(
            id=eid,
            src=e["from"],
            dst=e["to"],
            type=e["type"],
            date=_date(e.get("date"), f"edge {eid} date"),
            source=e["source"],
            confidence=e["confidence"],
            size_usd=float(e["size_usd"]) if e.get("size_usd") is not None else None,
            status=e.get("status"),
            tenor=None if e.get("tenor") is None else str(e["tenor"]),
            share=float(share) if share is not None else None,
            note=e.get("note"),
            verified_at=_date(e["verified_at"], f"edge {eid} verified_at") if e.get("verified_at") else None,
        )
        (edges if edge.verified_at else drafts).append(edge)
    return Graph(nodes=nodes, edges=edges, drafts=drafts, corrections=list(raw.get("corrections") or []))


def load_graph_file() -> Graph:
    with open(CONFIG_DIR / "graph.yaml") as f:
        return load_graph(yaml.safe_load(f) or {})


def write_graph(conn, g: Graph, today: date, stale_days: int) -> None:
    """Replace the nodes and edges tables with the file's verified graph."""
    with conn.cursor() as cur:
        cur.execute("delete from edges")
        cur.execute("delete from nodes")
        cur.executemany(
            "insert into nodes (key, name, kind, public, markets, note) values (%s, %s, %s, %s, %s, %s)",
            [(n.key, n.name, n.kind, n.public, list(n.markets), n.note) for n in g.nodes.values()],
        )
        cur.executemany(
            """insert into edges (id, src, dst, type, size_usd, status, tenor, share, date, source, confidence,
                                  note, verified_at, stale)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    e.id,
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
                    e.note,
                    e.verified_at,
                    e.stale(today, stale_days),
                )
                for e in g.edges
            ],
        )
        cur.executemany(
            "insert into graph_corrections (date, edge, change) values (%s, %s, %s) on conflict do nothing",
            [(_date(c["date"], "correction date"), c.get("edge"), c["change"]) for c in g.corrections],
        )
        cur.execute(
            """insert into definitions (key, value, updated_at) values ('graph', %s, now())
               on conflict (key) do update set value = excluded.value, updated_at = now()""",
            (json.dumps({"edges": len(g.edges), "drafts": len(g.drafts), "stale_days": stale_days}),),
        )
