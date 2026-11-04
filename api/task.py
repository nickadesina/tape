"""Vercel serves this file at /api/task; the app routes on the path (tape/web.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tape.web import app  # noqa: E402

__all__ = ["app"]
