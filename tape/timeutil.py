from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(UTC)


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def et_day(dt: datetime) -> date:
    return dt.astimezone(ET).date()


def et_hour(dt: datetime) -> int:
    return dt.astimezone(ET).hour


def hour_key(dt: datetime) -> str:
    """URL-safe hour id used in chart permalinks: 2026-10-31T19Z."""
    return floor_hour(dt).strftime("%Y-%m-%dT%HZ")


def parse_hour_key(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%HZ").replace(tzinfo=UTC)


def et_hour_on(day: date, hour: int) -> datetime:
    """The UTC instant of `hour`:00 ET on `day` (DST-aware)."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(UTC)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def from_ms(t: int) -> datetime:
    return datetime.fromtimestamp(t / 1000, tz=UTC)


def hours_between(a: datetime, b: datetime) -> list[datetime]:
    out, h = [], floor_hour(a)
    while h <= b:
        out.append(h)
        h += timedelta(hours=1)
    return out
