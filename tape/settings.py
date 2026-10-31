"""Configuration: two YAML files in config/ plus environment variables.

YAML holds what the tape means (universe, thresholds). The environment holds
where it runs (database, Telegram, chart storage). Nothing else is read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("TAPE_CONFIG_DIR", ROOT / "config"))


@dataclass(frozen=True)
class Leg:
    key: str
    label: str
    markets: tuple[str, ...]


@dataclass(frozen=True)
class UniverseConfig:
    dexes: tuple[str, ...]
    legs: dict[str, Leg]
    leg_order: tuple[str, ...]
    candidates: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]

    def leg_of(self, coin: str) -> str | None:
        for leg in self.legs.values():
            if coin in leg.markets:
                return leg.key
        return None


@dataclass(frozen=True)
class Env:
    database_url: str
    telegram_token: str | None
    telegram_chat_id: str | None
    site_url: str
    chart_store: str  # db | s3 | local
    chart_dir: Path
    s3_bucket: str | None
    s3_endpoint: str | None
    s3_public_base: str | None
    info_url: str
    ws_url: str


@dataclass(frozen=True)
class Settings:
    universe: UniverseConfig
    tape: dict[str, Any]
    env: Env = field(repr=False)

    def t(self, *path: str) -> Any:
        node: Any = self.tape
        for p in path:
            node = node[p]
        return node


def _load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def load_universe_config(raw: dict[str, Any] | None = None) -> UniverseConfig:
    raw = raw if raw is not None else _load_yaml("universe.yaml")
    legs = {
        key: Leg(key=key, label=v["label"], markets=tuple(v["markets"]))
        for key, v in raw["legs"].items()
    }
    seen: dict[str, str] = {}
    for leg in legs.values():
        for m in leg.markets:
            if m in seen:
                raise ValueError(f"{m} is in two legs: {seen[m]} and {leg.key}")
            seen[m] = leg.key
    order = tuple(raw.get("leg_order") or legs.keys())
    missing = set(legs) - set(order)
    if missing:
        raise ValueError(f"leg_order is missing {sorted(missing)}")
    return UniverseConfig(
        dexes=tuple(raw["dexes"]),
        legs=legs,
        leg_order=order,
        candidates=tuple(raw.get("candidates") or ()),
        aliases={k: tuple(v) for k, v in (raw.get("aliases") or {}).items()},
    )


def load_env() -> Env:
    return Env(
        database_url=os.environ.get("DATABASE_URL", "postgresql://tape:tape@localhost/tape"),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        site_url=os.environ.get("SITE_URL", "https://openmarket.systems/tape").rstrip("/"),
        chart_store=os.environ.get("CHART_STORE", "db"),
        chart_dir=Path(os.environ.get("CHART_DIR", ROOT / "var" / "charts")),
        s3_bucket=os.environ.get("S3_BUCKET") or None,
        s3_endpoint=os.environ.get("S3_ENDPOINT") or None,
        s3_public_base=(os.environ.get("S3_PUBLIC_BASE") or "").rstrip("/") or None,
        info_url=os.environ.get("HL_INFO_URL", "https://api.hyperliquid.xyz/info"),
        ws_url=os.environ.get("HL_WS_URL", "wss://api.hyperliquid.xyz/ws"),
    )


def load() -> Settings:
    return Settings(universe=load_universe_config(), tape=_load_yaml("tape.yaml"), env=load_env())
