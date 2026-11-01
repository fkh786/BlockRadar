from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BAR_SECONDS = 300  # base timeframe: 5-minute candles


@dataclass
class Candle:
    t: int          # bar start, epoch seconds (UTC)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    oi: float = 0.0

    @property
    def day(self):
        return datetime.fromtimestamp(self.t, IST).date()


@dataclass
class StockData:
    """Everything the engine needs for one instrument, filled in by a data provider."""
    symbol: str
    name: str = ""
    sector: str = "Other"
    kind: str = "stock"      # "stock" or "index"
    candles: list[Candle] = field(default_factory=list)  # 5-min, oldest first, several days
    ltp: float = 0.0
    prev_close: float = 0.0
    day_volume: float = 0.0
    oi: float = 0.0          # current futures open interest (0 if unknown)
    oi_base: float = 0.0     # futures OI at the start of the session (0 if unknown)
    lot_size: int = 0
    updated: float = 0.0     # epoch seconds of last data update


def now_ist() -> datetime:
    return datetime.now(IST)


def session_open_ts(d) -> int:
    return int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())


def aggregate(candles: list[Candle], minutes: int) -> list[Candle]:
    """Combine 5-min candles into N-minute candles anchored at 09:15 each session."""
    if minutes <= 5:
        return candles
    out: list[Candle] = []
    key = None
    for c in candles:
        d = c.day
        t0 = session_open_ts(d)
        bucket = (c.t - t0) // (minutes * 60)
        k = (d, bucket)
        if k != key:
            out.append(Candle(t=t0 + bucket * minutes * 60, o=c.o, h=c.h, l=c.l, c=c.c, v=c.v, oi=c.oi))
            key = k
        else:
            b = out[-1]
            b.h = max(b.h, c.h)
            b.l = min(b.l, c.l)
            b.c = c.c
            b.v += c.v
            b.oi = c.oi
    return out
