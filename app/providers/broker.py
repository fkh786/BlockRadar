"""
Shared logic for live broker connectors (Fyers, Dhan, Zerodha).

A connector only has to implement a handful of hooks:
    _universe()          -> {symbol: meta}  (F&O stocks + NIFTY/BANKNIFTY)
    _history(sym, m, d0, d1) -> 5-min spot candles between two dates (inclusive)
    _fut_oi(sym, m)      -> (oi at start of session, latest oi) from the near-month future
    _quotes(syms, extra) -> ({sym: {"ltp","prev_close","volume","oi"}}, {extra_key: ltp})
    _chain(sym, m, expiry) -> {"expiry","spot","rows":[{"strike","CE":{...},"PE":{...}}]}
MCX commodities (optional, two more hooks):
    _mcx_contracts()     -> [{"sym","type":"FUT"|"CE"|"PE","expiry":date,"strike","key","label","meta"}]
                            ("meta" on futures = the connector's own fields for _history/_fut_oi/_quotes)
    _quote_keys(keys)    -> {key: {"ltp","oi","volume"}} for option contracts
Everything else - pacing calls under the broker's rate limits, building the live
5-min candle from quotes, rotating candle refreshes, daily reloads - lives here.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .. import sessions
from ..models import BAR_SECONDS, IST, Candle, StockData, now_ist
from ..options import fill_greeks
from ..universe import INDICES, MCX, MCX_ROLL_DAYS, sector_of
from .base import Provider

MCX_CHAIN_STRIKES = 12     # strikes either side of the future price in the MCX option helper


class LoginRequired(Exception):
    pass


class BrokerError(Exception):
    pass


class Limiter:
    """Sliding-window limiter: [(window_seconds, max_calls), ...]."""

    def __init__(self, limits):
        self.limits = limits
        self.longest = max(w for w, _ in limits)
        self.calls: deque = deque()
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            while True:
                now = time.monotonic()
                while self.calls and now - self.calls[0] > self.longest:
                    self.calls.popleft()
                if all(sum(1 for t in self.calls if now - t <= w) < cap for w, cap in self.limits):
                    self.calls.append(now)
                    return
                await asyncio.sleep(0.15)

    def used(self, window):
        now = time.monotonic()
        return sum(1 for t in self.calls if now - t <= window)


def market_open_now() -> bool:
    """NSE cash/F&O session."""
    return sessions.nse_open()


def mcx_symbol(*candidates) -> str | None:
    """Which of our MCX commodities a contract belongs to (first candidate that matches)."""
    for v in candidates:
        v = (v or "").replace(" ", "").upper()
        if v in MCX:
            return v
    return None


def session_end(m: dict) -> str:
    """Last candle time to ask a broker for: 23:55 for MCX, 15:30 for NSE."""
    return "23:59:00" if m.get("kind") == "commodity" else "15:30:00"


class BrokerProvider(Provider):
    key = "broker"
    label = "Broker"
    refresh_seconds = 60
    LIMITS = [(1, 5), (60, 150)]
    HISTORY_DAYS = 20
    KEEP_SESSIONS = 10
    ROTATE_PER_CYCLE = 8
    LOAD_CONCURRENCY = 4
    CHAIN_TTL = 30
    OI_FROM_QUOTES = True       # False: refresh futures OI via _fut_oi for top-ranked stocks

    def __init__(self, data_dir: Path, creds: dict):
        super().__init__()
        self.data_dir = data_dir
        self.creds = creds or {}
        self.limiter = Limiter(self.LIMITS)
        self.client: httpx.AsyncClient | None = None
        self.meta: dict[str, dict] = {}
        self.extra_ltp: dict[str, float] = {}
        self.session_day: date | None = None
        self._rotate_pos = 0
        self._last_full: dict[str, float] = {}
        self._chain_cache: dict[tuple, tuple] = {}
        self.needs_login = not self.has_token()
        self.loaded_at = 0.0
        self.mcx_error = ""

    # ------------------------------------------------------------- hooks
    def has_token(self) -> bool:
        return bool(self.creds.get("token"))

    async def _universe(self) -> dict[str, dict]:
        raise NotImplementedError

    async def _history(self, sym: str, m: dict, d0: date, d1: date) -> list[Candle]:
        raise NotImplementedError

    async def _fut_oi(self, sym: str, m: dict) -> tuple[float, float]:
        return 0.0, 0.0

    async def _quotes(self, syms: list[str], extra: list[str]):
        raise NotImplementedError

    async def _chain(self, sym: str, m: dict, expiry: str) -> dict:
        return {"expiry": expiry, "spot": 0, "rows": []}

    async def _mcx_contracts(self) -> list[dict]:
        return []            # connector doesn't support MCX

    async def _quote_keys(self, keys: list[str]) -> dict:
        return {}

    # ------------------------------------------------------------- helpers
    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        return self.client

    async def _download_cached(self, url: str, name: str, headers: dict | None = None) -> bytes:
        """Download a daily instrument file once per day."""
        path = self.data_dir / f"{self.key}_{name}_{date.today():%Y%m%d}"
        if not path.exists():
            self.message = f"Downloading {self.label} instrument list…"
            try:
                r = await self._client().get(url, headers=headers or {}, timeout=180)
            except httpx.HTTPError as e:
                raise BrokerError(f"Could not download the {self.label} instrument list ({e.__class__.__name__}). "
                                  f"Check your internet connection.")
            if r.status_code != 200:
                raise BrokerError(f"Could not download the {self.label} instrument list (HTTP {r.status_code}).")
            for old in self.data_dir.glob(f"{self.key}_{name}_*"):
                old.unlink(missing_ok=True)
            path.write_bytes(r.content)
        return path.read_bytes()

    def _new_stock(self, sym: str, m: dict) -> StockData:
        if m.get("kind") == "commodity":
            return StockData(symbol=sym, name=MCX[sym][3], sector=MCX[sym][0], kind="commodity",
                             lot_size=MCX[sym][1])
        if m.get("kind") == "index":
            name = INDICES[sym][0]
        else:
            name = m.get("name") or sym
        return StockData(symbol=sym, name=name, sector=sector_of(sym), kind=m.get("kind", "stock"),
                         lot_size=int(m.get("lot") or 0))

    def _build_mcx(self, rows: list[dict]) -> dict[str, dict]:
        """Near-month future per commodity (rolled MCX_ROLL_DAYS before expiry) + its option contracts."""
        today = now_ist().date()
        roll_cut = today + timedelta(days=MCX_ROLL_DAYS)
        futs: dict[str, list] = {}
        opts: dict[str, dict[str, list]] = {}
        for x in rows:
            if x["sym"] not in MCX or not x.get("expiry") or x["expiry"] < today:
                continue
            if x["type"] == "FUT":
                futs.setdefault(x["sym"], []).append(x)
            elif x["type"] in ("CE", "PE"):
                opts.setdefault(x["sym"], {}).setdefault(x["expiry"].isoformat(), []).append(
                    (float(x["strike"]), x["type"], x["key"]))
        meta = {}
        for sym, lst in futs.items():
            lst.sort(key=lambda r: r["expiry"])
            f = next((r for r in lst if r["expiry"] >= roll_cut), lst[0])
            meta[sym] = {**f.get("meta", {}), "kind": "commodity", "lot": MCX[sym][1], "name": MCX[sym][3],
                         "contract": f.get("label") or f"{sym} FUT", "fut_expiry": f["expiry"].isoformat(),
                         "expiries": sorted(opts.get(sym, {})), "mcx_opts": opts.get(sym, {})}
        return meta

    # ------------------------------------------------------------- loading
    async def start(self):
        self.ready = False
        if not self.has_token():
            self.needs_login = True
            self.message = f"Connect your {self.label} account to load live data"
            return
        self.meta = await self._universe()
        if not self.meta:
            raise BrokerError("No F&O instruments found in the instrument list")
        try:
            mcx = self._build_mcx(await self._mcx_contracts())
            self.meta.update(mcx)
            self.mcx_error = "" if mcx else f"MCX commodities aren't available through {self.label} here"
        except LoginRequired:
            raise
        except Exception as e:                 # MCX trouble must never stop the NSE board
            self.mcx_error = f"MCX list failed: {e}"
            print(f"[{self.key}] {self.mcx_error}")
        self.loaded_at = time.time()
        self.stocks = {s: self._new_stock(s, m) for s, m in self.meta.items()}
        syms = sorted(self.meta, key=lambda s: (self.meta[s].get("kind") != "index", s))
        self.progress = [0, len(syms)]
        self.message = f"Loading candles for {len(syms)} instruments…"
        today = now_ist().date()
        d0 = today - timedelta(days=self.HISTORY_DAYS)
        sem = asyncio.Semaphore(self.LOAD_CONCURRENCY)
        failures = []

        async def load(sym):
            async with sem:
                try:
                    await self._load_one(sym, d0, today)
                except LoginRequired:
                    raise
                except Exception as e:
                    failures.append(sym)
                    print(f"[{self.key}] {sym}: {e}")
                self.progress[0] += 1

        await asyncio.gather(*(load(s) for s in syms))
        if failures and len(failures) == len(syms):
            raise BrokerError(f"Could not load candles for any instrument (first error logged in the window)")
        days = [sd.candles[-1].day for sd in self.stocks.values() if sd.candles and sd.kind != "commodity"]
        self.session_day = max(days) if days else today
        try:
            await self._apply_quotes()
        except LoginRequired:
            raise
        except Exception as e:
            print(f"[{self.key}] quotes: {e}")
        self.ready = True
        self.needs_login = False
        self.message = f"Live data from {self.label}" + (f" ({len(failures)} instruments failed to load)" if failures else "")

    async def _load_one(self, sym: str, d0: date, d1: date):
        m = self.meta[sym]
        sd = self.stocks[sym]
        candles = await self._history(sym, m, d0, d1)
        candles.sort(key=lambda c: c.t)
        sessions = sorted({c.day for c in candles})[-self.KEEP_SESSIONS:]
        keep = set(sessions)
        sd.candles = [c for c in candles if c.day in keep]
        if sd.candles:
            last_day = sd.candles[-1].day
            prior = [c for c in sd.candles if c.day < last_day]
            sd.prev_close = prior[-1].c if prior else sd.candles[0].o
            sd.ltp = sd.candles[-1].c
            sd.day_volume = sum(c.v for c in sd.candles if c.day == last_day)
        self._last_full[sym] = time.time()
        try:
            sd.oi_base, sd.oi = await self._fut_oi(sym, m)
        except LoginRequired:
            raise
        except Exception as e:
            print(f"[{self.key}] {sym} OI: {e}")
        sd.updated = time.time()

    # ------------------------------------------------------------- live updates
    def _open_syms(self, nse: bool, mcx: bool) -> list[str]:
        return [s for s, m in self.meta.items() if (mcx if m.get("kind") == "commodity" else nse)]

    async def _apply_quotes(self, nse: bool = True, mcx: bool = True):
        syms = self._open_syms(nse, mcx)
        if not syms and not self.watch_keys:
            return
        data, extra = await self._quotes(syms, sorted(self.watch_keys))
        now = time.time()
        live = {"stock": sessions.nse_open(), "index": sessions.nse_open(), "commodity": sessions.mcx_open()}
        bucket = int(now // BAR_SECONDS * BAR_SECONDS)
        for sym, q in data.items():
            sd = self.stocks.get(sym)
            if not sd or not q.get("ltp"):
                continue
            sd.ltp = float(q["ltp"])
            if q.get("prev_close"):
                sd.prev_close = float(q["prev_close"])
            if q.get("volume") is not None:
                sd.day_volume = float(q["volume"])
            if q.get("oi"):
                sd.oi = float(q["oi"])
            if live.get(sd.kind) and sd.candles:
                self._update_forming(sd, bucket, sd.ltp)
            sd.updated = now
        self.extra_ltp.update(extra)

    @staticmethod
    def _update_forming(sd: StockData, bucket: int, ltp: float):
        last = sd.candles[-1]
        today = datetime.fromtimestamp(bucket, IST).date()
        if last.t == bucket:
            last.h, last.l, last.c = max(last.h, ltp), min(last.l, ltp), ltp
        elif last.t < bucket:
            last = Candle(t=bucket, o=ltp, h=ltp, l=ltp, c=ltp)
            sd.candles.append(last)
        else:
            return
        before = sum(c.v for c in sd.candles[:-1] if c.day == today)
        last.v = max(0.0, sd.day_volume - before)

    async def refresh(self):
        if not self.has_token() or self.needs_login:
            return
        n = now_ist()
        today = n.date()
        nse, mcx = sessions.nse_open(n), sessions.mcx_open(n)
        if not (nse or mcx):
            return
        # first refresh after 09:15 on a new trading day: full reload (MCX runs on live
        # quotes between 09:00 and 09:15, then gets reloaded with everything else)
        reload_at = datetime(n.year, n.month, n.day, 9, 15, tzinfo=IST).timestamp()
        if nse and self.loaded_at and self.loaded_at < reload_at:
            await self.start()
            return
        await self._apply_quotes(nse, mcx)
        now = time.time()
        open_syms = set(self._open_syms(nse, mcx))
        picks = [s for s in self.priority if s in open_syms and now - self._last_full.get(s, 0) > 300][: self.ROTATE_PER_CYCLE // 2]
        syms = sorted(open_syms)
        while len(picks) < min(self.ROTATE_PER_CYCLE, len(syms)):
            s = syms[self._rotate_pos % len(syms)]
            self._rotate_pos += 1
            if s not in picks:
                picks.append(s)
        for s in picks:
            try:
                fresh = await self._history(s, self.meta[s], today, today)
            except LoginRequired:
                raise
            except Exception as e:
                print(f"[{self.key}] refresh {s}: {e}")
                continue
            if fresh:
                fresh.sort(key=lambda c: c.t)
                sd = self.stocks[s]
                sd.candles = [c for c in sd.candles if c.t < fresh[0].t] + fresh
                if sd.ltp:
                    self._update_forming(sd, int(now // BAR_SECONDS * BAR_SECONDS), sd.ltp)
                self._last_full[s] = now
            if not self.OI_FROM_QUOTES and s in self.priority:
                try:
                    sd = self.stocks[s]
                    base, cur = await self._fut_oi(s, self.meta[s])
                    if cur:
                        sd.oi_base, sd.oi = base or sd.oi_base, cur
                except LoginRequired:
                    raise
                except Exception as e:
                    print(f"[{self.key}] OI {s}: {e}")

    # ------------------------------------------------------------- options
    def expiries(self, symbol: str) -> list[str]:
        today = now_ist().date().isoformat()
        return [e for e in (self.meta.get(symbol, {}).get("expiries") or []) if e >= today][:6]

    async def option_chain(self, symbol: str, expiry: str) -> dict:
        m = self.meta.get(symbol)
        if not m:
            return {"expiry": expiry, "spot": 0, "rows": []}
        ck = (symbol, expiry)
        hit = self._chain_cache.get(ck)
        if hit and time.time() - hit[0] < self.CHAIN_TTL:
            return hit[1]
        commodity = m.get("kind") == "commodity"
        chain = await (self._mcx_chain(symbol, m, expiry) if commodity else self._chain(symbol, m, expiry))
        if not chain.get("spot"):
            chain["spot"] = self.stocks[symbol].ltp
        chain["rows"] = sorted(chain.get("rows") or [], key=lambda r: r["strike"])
        if commodity:      # options on futures: Black-76 = Black-Scholes on the future with r = 0
            fill_greeks(chain, r=0.0, close=sessions.mcx_close(date.fromisoformat(expiry)))
        else:
            fill_greeks(chain)
        for row in chain["rows"]:
            for cp in ("CE", "PE"):
                o = row.get(cp)
                if o and o.get("key") and o.get("ltp"):
                    self.extra_ltp[o["key"]] = float(o["ltp"])
        self._chain_cache[ck] = (time.time(), chain)
        return chain

    async def _mcx_chain(self, symbol: str, m: dict, expiry: str) -> dict:
        """Brokers' option-chain APIs don't cover MCX, so build the chain from the instrument
        list + one quote call. Premiums are live; IV and Greeks are filled in afterwards."""
        contracts = (m.get("mcx_opts") or {}).get(expiry) or []
        spot = self.stocks[symbol].ltp
        if not contracts:
            return {"expiry": expiry, "spot": spot, "rows": []}
        strikes = set(sorted({k for k, _, _ in contracts}, key=lambda k: abs(k - spot))[:2 * MCX_CHAIN_STRIKES + 1])
        wanted = [c for c in contracts if c[0] in strikes]
        q = await self._quote_keys([key for _, _, key in wanted])
        rows: dict[float, dict] = {}
        for k, cp, key in wanted:
            v = q.get(key) or {}
            rows.setdefault(k, {"strike": k})[cp] = {
                "key": key, "ltp": float(v.get("ltp") or 0), "oi": float(v.get("oi") or 0), "prev_oi": 0,
                "volume": float(v.get("volume") or 0), "iv": 0, "delta": None, "gamma": None, "theta": 0}
        return {"expiry": expiry, "spot": spot, "rows": list(rows.values())}

    def option_price(self, key: str) -> float | None:
        return self.extra_ltp.get(key)

    def is_open(self, kind: str) -> bool:
        return sessions.is_open(kind)

    async def stop(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    def market(self) -> dict:
        n = now_ist()
        open_ = market_open_now()
        return {"open": open_, "clock": n.strftime("%H:%M"),
                "session": self.session_day.isoformat() if self.session_day else "",
                "label": "Market open" if open_ else "Market closed - showing last session",
                "api_calls_min": self.limiter.used(60), "mcx": self.mcx_market()}

    def mcx_market(self) -> dict:
        n = now_ist()
        open_ = sessions.mcx_open(n)
        h, mm = sessions.mcx_close(n.date())
        return {"open": open_, "hours": sessions.hours_label(n.date()), "close": f"{h:02d}:{mm:02d}",
                "label": "MCX open" if open_ else "MCX closed - showing last session",
                "error": self.mcx_error}


def parse_ts(v) -> int:
    """Epoch seconds from an epoch number or an ISO string (handles +0530)."""
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 1e11 else int(v)
    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        return n // 1000 if n > 1e11 else n
    if len(s) >= 5 and (s[-5] in "+-") and s[-4:].isdigit():
        s = s[:-2] + ":" + s[-2:]
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return int(dt.timestamp())


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def token_valid_until_reset(saved_iso: str, reset_hour: int, reset_minute: int = 0) -> bool:
    """True if the token was saved after the most recent daily reset time (IST)."""
    try:
        t = datetime.fromisoformat(saved_iso)
    except Exception:
        return False
    n = now_ist()
    reset = n.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
    if n < reset:
        reset -= timedelta(days=1)
    return t >= reset


def log_exc(prefix: str):
    print(prefix)
    traceback.print_exc()
