"""Exchange trading hours (IST)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import now_ist

NSE_OPEN, NSE_CLOSE = (9, 15), (15, 30)
MCX_OPEN = (9, 0)


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def us_dst(d: date) -> bool:
    """US daylight saving: second Sunday of March to first Sunday of November."""
    return _nth_sunday(d.year, 3, 2) <= d < _nth_sunday(d.year, 11, 1)


def mcx_close(d: date) -> tuple[int, int]:
    """MCX non-agri commodities close at 23:55 during US daylight saving, else 23:30."""
    return (23, 55) if us_dst(d) else (23, 30)


def _between(n: datetime, start, end) -> bool:
    m = n.hour * 60 + n.minute
    return start[0] * 60 + start[1] <= m < end[0] * 60 + end[1]


def nse_open(n: datetime | None = None) -> bool:
    n = n or now_ist()
    return n.weekday() < 5 and _between(n, NSE_OPEN, NSE_CLOSE)


def mcx_open(n: datetime | None = None) -> bool:
    n = n or now_ist()
    return n.weekday() < 5 and _between(n, MCX_OPEN, mcx_close(n.date()))


def is_open(kind: str, n: datetime | None = None) -> bool:
    return mcx_open(n) if kind == "commodity" else nse_open(n)


def anchor(kind: str) -> tuple[int, int]:
    """Session start used to line up 15-min / 1-hour candles."""
    return MCX_OPEN if kind == "commodity" else NSE_OPEN


def eod_time(kind: str, d: date | None = None) -> str:
    """When open paper trades are squared off (10 minutes before the close)."""
    if kind == "commodity":
        h, m = mcx_close(d or now_ist().date())
        t = datetime(2000, 1, 1, h, m) - timedelta(minutes=10)
        return t.strftime("%H:%M")
    return "15:20"


def hours_label(d: date | None = None) -> str:
    h, m = mcx_close(d or now_ist().date())
    return f"09:00–{h:02d}:{m:02d}"
