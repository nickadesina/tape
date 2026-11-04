"""The worker's HTTP entrypoint on Vercel: a bare ASGI app (no framework).

Vercel Cron calls the job paths with `Authorization: Bearer $CRON_SECRET`;
every path but /api/health requires it.

  GET  /api/health          liveness, no secret needed
  GET  /api/shift           one WebSocket shift (~11.5 min)
  GET  /api/sweep           advance this hour's sweep, then metrics
  GET  /api/daily[?force=1] the 16:00 ET post
  POST /api/task            {"args": ["status"]}: run a CLI command
"""

from __future__ import annotations

import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import asyncio  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402
from urllib.parse import parse_qs  # noqa: E402

from . import settings as settings_mod  # noqa: E402
from . import worker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("tape.web")


def _authorized(headers: dict[str, str]) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return False
    return hmac.compare_digest(headers.get("authorization", ""), f"Bearer {secret}")


async def _body(receive) -> bytes:
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            return b"".join(chunks)


async def _send_json(send, status: int, payload) -> None:
    body = json.dumps(payload, default=str, indent=1).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    started = time.monotonic()
    path = scope["path"].rstrip("/")
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    query = {k: v[-1] for k, v in parse_qs(scope.get("query_string", b"").decode()).items()}
    if path == "/api/health":
        await _send_json(send, 200, {"ok": True})
        return
    if not _authorized(headers):
        await _send_json(send, 401, {"error": "unauthorized"})
        return
    s = settings_mod.load()
    try:
        if path == "/api/shift":
            out = await worker.shift(s)
        elif path == "/api/sweep":
            out = await worker.sweep(s, started)
        elif path == "/api/daily":
            out = await asyncio.to_thread(worker.daily_job, s, query.get("force") == "1")
        elif path == "/api/task" and scope["method"] == "POST":
            args = json.loads(await _body(receive) or b"{}").get("args") or []
            out = await asyncio.to_thread(worker.task, [str(a) for a in args])
        else:
            await _send_json(send, 404, {"error": "not found"})
            return
    except Exception as e:
        log.exception("%s failed", path)
        await _send_json(send, 500, {"error": f"{type(e).__name__}: {e}"})
        return
    await _send_json(send, 200, out)
