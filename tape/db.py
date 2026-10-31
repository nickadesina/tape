"""Postgres access: connections and migrations."""

from __future__ import annotations

import logging
from importlib import resources

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)


def connect(dsn: str, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=autocommit, row_factory=dict_row)


async def aconnect(dsn: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)


def migrate(dsn: str) -> list[str]:
    applied: list[str] = []
    with connect(dsn) as conn:
        conn.execute(
            "create table if not exists schema_migrations (version text primary key, applied_at timestamptz not null default now())"
        )
        done = {r["version"] for r in conn.execute("select version from schema_migrations")}
        files = sorted(
            (f for f in resources.files("tape.migrations").iterdir() if f.name.endswith(".sql")),
            key=lambda f: f.name,
        )
        for f in files:
            version = f.name.removesuffix(".sql")
            if version in done:
                continue
            log.info("applying %s", f.name)
            conn.execute(f.read_text())
            conn.execute("insert into schema_migrations (version) values (%s)", (version,))
            applied.append(version)
        conn.commit()
    return applied
