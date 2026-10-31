"""Small shared persistence helpers: the markets table and the chart store."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .settings import Env, UniverseConfig
from .universe import Universe, resolve

log = logging.getLogger(__name__)


def load_universe(cfg: UniverseConfig, metas: dict[str, list[Any]]) -> Universe:
    return resolve(cfg, metas)


async def write_markets(conn, cfg: UniverseConfig, u: Universe) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into legs (key, label, ord) values (%s, %s, %s) on conflict (key) do update set label = excluded.label, ord = excluded.ord",
            [(k, cfg.legs[k].label, i) for i, k in enumerate(cfg.leg_order)],
        )
        await cur.execute("update markets set active = false, updated_at = now()")
        await cur.executemany(
            """insert into markets (coin, dex, leg, max_leverage, active) values (%s, %s, %s, %s, true)
               on conflict (coin) do update set dex = excluded.dex, leg = excluded.leg,
               max_leverage = excluded.max_leverage, active = true, updated_at = now()""",
            [(m.coin, m.dex, m.leg, m.max_leverage) for m in u.markets.values()],
        )


class ChartStore:
    """Where rendered PNGs go. The `charts` table always records them.

    db     PNG bytes in the charts table; the site serves them. Simplest; prune
           with `render.hourly_retention_days` to stay inside a small Neon plan.
    s3     any S3-compatible bucket (R2, S3); the site redirects to
           S3_PUBLIC_BASE/<key>. Keeps every hour forever for a few cents.
    local  files under CHART_DIR, for development.
    """

    def __init__(self, env: Env):
        self.env = env
        self.kind = env.chart_store
        self._s3 = None
        if self.kind == "s3":
            import boto3  # optional dependency: pip install .[s3]

            self._s3 = boto3.client("s3", endpoint_url=env.s3_endpoint)

    @staticmethod
    def key(kind: str, subject: str, hour: datetime) -> str:
        from .timeutil import hour_key

        return f"charts/{kind}/{subject.replace(':', '-')}/{hour_key(hour)}.png"

    def put(self, conn, kind: str, subject: str, hour: datetime, png: bytes, size: tuple[int, int]) -> str:
        key = self.key(kind, subject, hour)
        data: bytes | None = None
        path: str | None = None
        if self.kind == "s3":
            self._s3.put_object(Bucket=self.env.s3_bucket, Key=key, Body=png, ContentType="image/png",
                                CacheControl="public, max-age=31536000, immutable")
            path = key
        elif self.kind == "local":
            p = self.env.chart_dir / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(png)
            path = str(p)
        else:
            data = png
        conn.execute(
            """insert into charts (kind, subject, hour, path, bytes, width, height) values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (kind, subject, hour) do update set path = excluded.path, bytes = excluded.bytes,
               width = excluded.width, height = excluded.height, created_at = now()""",
            (kind, subject, hour, path, data, size[0], size[1]),
        )
        return key

    def get(self, conn, kind: str, subject: str, hour: datetime) -> bytes | None:
        row = conn.execute(
            "select path, bytes from charts where kind = %s and subject = %s and hour = %s", (kind, subject, hour)
        ).fetchone()
        if not row:
            return None
        if row["bytes"] is not None:
            return bytes(row["bytes"])
        if self.kind == "s3":
            return self._s3.get_object(Bucket=self.env.s3_bucket, Key=row["path"])["Body"].read()
        if self.kind == "local":
            from pathlib import Path

            return Path(row["path"]).read_bytes()
        return None
