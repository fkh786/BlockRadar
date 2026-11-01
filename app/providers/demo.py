"""
Demo mode: a simulated market so the whole app can be tried without a broker.
Prices are synthetic (random walks with trend / pullback phases, sector and
market moves, volume and futures OI). Option chains are priced with
Black-Scholes from the simulated price. Nothing here is real market data.
"""
from __future__ import annotations

import math
import random
import time
from datetime import date, datetime, timedelta

from ..models import IST, Candle, StockData, session_open_ts
from ..options import bs, demo_expiries, years_to_expiry
from ..universe import INDICES, STOCKS, demo_strike_step
from .base import Provider

SESSION_MINUTES = 375          # 09:15 -> 15:30
PRIOR_DAYS = 9


def _weekdays_back(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


class _Path:
    """One instrument's 1-minute bars for one session."""

    def __init__(self, rng: random.Random, start_price: float, daily_vol: float, bias: float,
                 base_vol: float, oi_start: float, oi_mode: int):
        self.minutes: list[tuple] = []   # (o, h, l, c, v, oi)
        per_min = daily_vol / math.sqrt(SESSION_MINUTES) * 0.75
        rate = abs(bias) * daily_vol / SESSION_MINUTES   # average drift per minute
        p = start_price
        oi = oi_start
        m = 0
        direction = 1 if bias >= 0 else -1
        while m < SESSION_MINUTES:
            impulse = rng.random() < 0.55
            length = rng.randint(12, 40) if impulse else rng.randint(8, 30)
            if abs(bias) < 0.15:                  # choppy: random small phases
                d = rng.choice((-1, 1)) * daily_vol * rng.uniform(0.05, 0.2) / length
            elif impulse:
                d = direction * rate * rng.uniform(1.6, 2.8)
            else:
                d = -direction * rate * rng.uniform(0.8, 1.6)
            for _ in range(length):
                if m >= SESSION_MINUTES:
                    break
                ushape = 1.8 - 1.4 * math.sin(math.pi * m / SESSION_MINUTES)
                o = p
                ticks = [o]
                for _ in range(4):
                    ticks.append(ticks[-1] * (1 + d / 4 + rng.gauss(0, per_min / 2)))
                c = ticks[-1]
                h = max(ticks) * (1 + abs(rng.gauss(0, per_min / 4)))
                l = min(ticks) * (1 - abs(rng.gauss(0, per_min / 4)))
                ret = abs(c / o - 1) / per_min if per_min else 0
                v = base_vol * ushape * (0.6 + 0.4 * rng.random() + 0.35 * ret)
                oi *= 1 + oi_mode * abs(rng.gauss(0.00012, 0.00025)) + rng.gauss(0, 0.0001)
                self.minutes.append((o, h, l, c, v, oi))
                p = c
                m += 1

    def candles_5m(self, session_t0: int, upto: int) -> list[Candle]:
        out = []
        mins = self.minutes[:upto]
        for b in range(0, len(mins), 5):
            chunk = mins[b:b + 5]
            out.append(Candle(
                t=session_t0 + b * 60,
                o=round(chunk[0][0], 2), h=round(max(x[1] for x in chunk), 2),
                l=round(min(x[2] for x in chunk), 2), c=round(chunk[-1][3], 2),
                v=round(sum(x[4] for x in chunk)), oi=round(chunk[-1][5]),
            ))
        return out


class DemoProvider(Provider):
    key = "demo"
    label = "Demo (simulated data)"
    refresh_seconds = 4            # one simulated minute every 4 real seconds

    def __init__(self, start_minute: int = 135):
        super().__init__()
        self.seed = int(date.today().strftime("%Y%m%d"))
        self.start_minute = start_minute
        self.sim_minute = start_minute
        self.sessions: list[date] = []
        self.paths: dict[str, list[_Path]] = {}
        self.vol: dict[str, float] = {}

    # ------------------------------------------------------------------ build
    def _build(self):
        rng_market = random.Random(self.seed)
        today = date.today()
        end = today if today.weekday() < 5 else today - timedelta(days=today.weekday() - 4)
        self.sessions = _weekdays_back(end, PRIOR_DAYS + 1)
        market_bias = [rng_market.gauss(0, 0.35) for _ in self.sessions]
        sectors = sorted({s for s, _ in STOCKS.values()})
        sector_bias = {(sec, i): rng_market.gauss(0, 0.45) for sec in sectors for i in range(len(self.sessions))}

        self.paths.clear()
        self.stocks.clear()
        universe = [(s, sec, base, "stock") for s, (sec, base) in STOCKS.items() if s != "ZOMATO"]
        universe += [(s, "Index", v[1], "index") for s, v in INDICES.items()]
        for sym, sector, base, kind in universe:
            rng = random.Random(f"{self.seed}-{sym}")
            if kind == "index":
                daily_vol = rng.uniform(0.007, 0.011)
                base_vol = 0.0
                oi = rng.uniform(1.2e7, 2.0e7)
            else:
                daily_vol = rng.uniform(0.012, 0.032)
                base_vol = rng.uniform(2_000, 60_000) * (1000 / max(base, 50)) ** 0.5
                oi = rng.uniform(2e6, 4e7)
            self.vol[sym] = daily_vol
            price = base * rng.uniform(0.97, 1.03) if kind == "index" else base * rng.uniform(0.9, 1.1)
            days = []
            for i, _ in enumerate(self.sessions):
                if kind == "index":
                    bias = market_bias[i] * 2 + rng.gauss(0, 0.3)
                else:
                    bias = market_bias[i] + sector_bias[(sector, i)] + rng.gauss(0, 0.6)
                    if rng.random() < 0.22:
                        bias *= 0.1                  # some stocks go nowhere
                bias = max(-1.8, min(1.8, bias))
                oi_mode = rng.choice((1, 1, -1))     # build-up is more common than unwinding
                gap = rng.gauss(0, daily_vol * 0.25)
                path = _Path(rng, price * (1 + gap), daily_vol, bias, base_vol, oi, oi_mode)
                days.append(path)
                price = path.minutes[-1][3]
                oi = path.minutes[-1][5]
            self.paths[sym] = days
            if kind == "index":
                name, lot = INDICES[sym][0], INDICES[sym][3]
            else:
                name, lot = sym, max(25, int(round(500000 / base / 25) * 25))
            self.stocks[sym] = StockData(symbol=sym, name=name, sector=sector, kind=kind, lot_size=lot)

    def _materialize(self):
        k = max(1, min(self.sim_minute, SESSION_MINUTES))
        now = time.time()
        for sym, days in self.paths.items():
            candles: list[Candle] = []
            for i, d in enumerate(self.sessions):
                t0 = session_open_ts(d)
                upto = SESSION_MINUTES if i < len(self.sessions) - 1 else k
                candles.extend(days[i].candles_5m(t0, upto))
            today_path = days[-1]
            last = today_path.minutes[k - 1]
            sd = self.stocks[sym]
            sd.candles = candles
            sd.ltp = round(last[3], 2)
            sd.prev_close = round(days[-2].minutes[-1][3], 2)
            sd.day_volume = sum(x[4] for x in today_path.minutes[:k])
            sd.oi = last[5]
            sd.oi_base = days[-2].minutes[-1][5]
            sd.updated = now

    # ------------------------------------------------------------------ API
    async def start(self):
        self.message = "Building simulated market…"
        self.progress = [0, 1]
        self._build()
        self._materialize()
        self.progress = [1, 1]
        self.ready = True
        self.message = "Demo market running (simulated prices)"

    async def refresh(self):
        self.sim_minute += 1
        if self.sim_minute > SESSION_MINUTES + 6:    # brief "closed" pause, then a new day
            self.seed += 1
            self.sim_minute = 20
            self._build()
        self._materialize()

    def _sim_now(self) -> datetime:
        d = self.sessions[-1] if self.sessions else date.today()
        m = min(self.sim_minute, SESSION_MINUTES)
        return datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST) + timedelta(minutes=m)

    def market(self) -> dict:
        m = min(self.sim_minute, SESSION_MINUTES)
        hh, mm = divmod(9 * 60 + 15 + m, 60)
        open_ = self.sim_minute < SESSION_MINUTES
        return {
            "open": open_,
            "clock": f"{hh:02d}:{mm:02d}",
            "session": self.sessions[-1].isoformat() if self.sessions else "",
            "label": "Demo market open (simulated)" if open_ else "Demo market closed (simulated)",
        }

    # ------------------------------------------------------------------ options
    def expiries(self, symbol: str) -> list[str]:
        weekly = symbol in INDICES and INDICES[symbol][5]
        today = self.sessions[-1] if self.sessions else None
        return demo_expiries(weekly, today)

    def _iv(self, sym: str, S: float, K: float) -> float:
        base = self.vol.get(sym, 0.015) * math.sqrt(252) * 0.95
        return base * (1 + 2.2 * abs(math.log(K / S)))

    def _step(self, sym: str, S: float) -> float:
        return INDICES[sym][2] if sym in INDICES else demo_strike_step(S)

    async def option_chain(self, symbol: str, expiry: str) -> dict:
        sd = self.stocks.get(symbol)
        if not sd:
            return {"expiry": expiry, "spot": 0, "rows": []}
        S = sd.ltp
        step = self._step(symbol, S)
        T = years_to_expiry(expiry, self._sim_now())
        atm = round(S / step) * step
        rng = random.Random(f"{self.seed}-{symbol}-{expiry}")
        base_oi = (sd.oi or 1e7) / 12
        rows = []
        for n in range(-12, 13):
            K = round(atm + n * step, 2)
            if K <= 0:
                continue
            row = {"strike": K}
            for cp in ("CE", "PE"):
                iv = self._iv(symbol, S, K)
                g = bs(S, K, T, iv, cp)
                away = (K - S) / (step * 6)
                skew = 1.5 if (cp == "CE" and K > S) or (cp == "PE" and K < S) else 0.6
                roundness = 1.8 if (K / step) % 5 == 0 else 1.0
                oi = base_oi * math.exp(-away * away) * skew * roundness * rng.uniform(0.7, 1.3)
                row[cp] = {
                    "key": f"DEMO|{symbol}|{expiry}|{K:g}|{cp}",
                    "ltp": round(g["price"], 2), "oi": round(oi), "prev_oi": round(oi * rng.uniform(0.75, 1.1)),
                    "volume": round(oi * rng.uniform(0.5, 3)), "iv": round(g["iv"], 2),
                    "delta": round(g["delta"], 4), "gamma": g["gamma"], "theta": round(g["theta"], 3),
                }
            rows.append(row)
        return {"expiry": expiry, "spot": S, "rows": rows}

    def option_price(self, key: str) -> float | None:
        try:
            _, sym, expiry, strike, cp = key.split("|")
        except ValueError:
            return None
        sd = self.stocks.get(sym)
        if not sd:
            return None
        K = float(strike)
        T = years_to_expiry(expiry, self._sim_now())
        return round(bs(sd.ltp, K, T, self._iv(sym, sd.ltp, K), cp)["price"], 2)
