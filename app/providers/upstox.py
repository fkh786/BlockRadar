"""
Live data from Upstox (API v2/v3).

How it stays inside Upstox's limits (50/s, 500/min, 2000 per 30 min):
  * start-up: ~3 calls per stock (5-min history, today's 5-min candles,
    futures OI) - about 650 calls, paced at ~8 per second.
  * MCX commodities (near-month futures) are loaded the same way; outside NSE hours
    only the MCX instruments are quoted and refreshed.
  * every minute: one or two "full quote" calls for all stocks + futures,
    which update the live 5-min candle, LTP, day volume and OI;
    plus a fresh candle download for ~10 stocks (top-ranked first, then
    round-robin) to correct the locally built candles.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import sessions
from ..models import BAR_SECONDS, IST, Candle, StockData, now_ist
from ..options import fill_greeks
from ..universe import INDICES, MCX, MCX_ROLL_DAYS, sector_of
from .base import Provider

API = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
MCX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
MCX_CHAIN_STRIKES = 12     # strikes either side of the future price in the MCX option helper
HISTORY_DAYS = 20          # calendar days of 5-min history to load (1-hour view needs ~10 sessions)
KEEP_SESSIONS = 10         # sessions kept in memory (9 prior + today)
ROTATE_PER_CYCLE = 10      # candle re-downloads per minute


from .broker import LoginRequired  # noqa: E402  (shared across all brokers)


class RateLimiter:
    def __init__(self, per_sec=8, per_min=400, per_30=1800):
        self.limits = ((1, per_sec), (60, per_min), (1800, per_30))
        self.calls: deque = deque()

    async def wait(self):
        while True:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 1800:
                self.calls.popleft()
            ok = True
            for window, cap in self.limits:
                if sum(1 for t in self.calls if now - t <= window) >= cap:
                    ok = False
                    break
            if ok:
                self.calls.append(now)
                return
            await asyncio.sleep(0.2)

    def used(self, window):
        now = time.monotonic()
        return sum(1 for t in self.calls if now - t <= window)


def market_open_now() -> bool:
    """NSE cash/F&O session."""
    return sessions.nse_open()


def _mcx_symbol(x: dict) -> str | None:
    """Which of our MCX commodities an instrument row belongs to."""
    for v in (x.get("underlying_symbol"), x.get("asset_symbol"), x.get("name"),
              (x.get("trading_symbol") or "").split(" ")[0]):
        v = (v or "").replace(" ", "").upper()
        if v in MCX:
            return v
    return None


def _parse_candles(rows) -> list[Candle]:
    out = []
    for r in rows or []:
        ts = int(datetime.fromisoformat(r[0]).timestamp())
        out.append(Candle(t=ts, o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                          v=float(r[5] or 0), oi=float(r[6]) if len(r) > 6 and r[6] is not None else 0.0))
    out.sort(key=lambda c: c.t)
    return out


class UpstoxProvider(Provider):
    key = "upstox"
    label = "Upstox (live)"
    refresh_seconds = 60

    def __init__(self, data_dir: Path, token: str | None):
        super().__init__()
        self.data_dir = data_dir
        self.token = token
        self.limiter = RateLimiter()
        self.client: httpx.AsyncClient | None = None
        self.meta: dict[str, dict] = {}       # symbol -> keys, lot size
        self.priority: list[str] = []         # set by the scanner (top-ranked symbols)
        self._rotate_pos = 0
        self._last_full: dict[str, float] = {}
        self.session_day: date | None = None
        self.last_quote_ok = 0.0
        self.extra_ltp: dict[str, float] = {}       # option premiums for watched keys
        self._chain_cache: dict[tuple, tuple] = {}
        self.needs_login = not token
        self.loaded_at = 0.0                        # epoch seconds of the last full load
        self.mcx_opts: dict[str, dict[str, list]] = {}   # symbol -> expiry -> [(strike, CE/PE, key, underlying key)]
        self.mcx_error = ""

    # ------------------------------------------------------------- http
    async def _get(self, path: str, params: dict | None = None, retry=True):
        if not self.token:
            raise LoginRequired()
        await self.limiter.wait()
        r = await self.client.get(API + path, params=params, headers={
            "Accept": "application/json", "Authorization": f"Bearer {self.token}"})
        if r.status_code == 401:
            self.needs_login = True
            raise LoginRequired()
        if r.status_code == 429 and retry:
            await asyncio.sleep(3)
            return await self._get(path, params, retry=False)
        r.raise_for_status()
        return r.json()

    async def _candles(self, key: str, unit: str, interval: int, to_d: date | None = None,
                       from_d: date | None = None) -> list[Candle]:
        k = quote(key, safe="")
        if to_d is None:
            path = f"/v3/historical-candle/intraday/{k}/{unit}/{interval}"
        else:
            path = f"/v3/historical-candle/{k}/{unit}/{interval}/{to_d.isoformat()}/{from_d.isoformat()}"
        js = await self._get(path)
        return _parse_candles((js.get("data") or {}).get("candles"))

    # ------------------------------------------------------------- universe
    async def _load_instruments(self, exchange: str = "NSE") -> list[dict]:
        url = MCX_INSTRUMENTS_URL if exchange == "MCX" else INSTRUMENTS_URL
        prefix = "instruments_mcx_" if exchange == "MCX" else "instruments_"
        cache = self.data_dir / f"{prefix}{date.today():%Y%m%d}.json.gz"
        if not cache.exists():
            self.message = f"Downloading Upstox {exchange} instrument list…"
            r = await self.client.get(url, follow_redirects=True, timeout=120)
            r.raise_for_status()
            for old in self.data_dir.glob(f"{prefix}*.json.gz"):
                if exchange == "NSE" and old.name.startswith("instruments_mcx_"):
                    continue
                old.unlink(missing_ok=True)
            cache.write_bytes(r.content)
        return json.loads(gzip.decompress(cache.read_bytes()))

    def _build_mcx(self, rows: list[dict]):
        """Add MCX commodity futures (near month, rolled MCX_ROLL_DAYS before expiry)."""
        today = now_ist().date()
        futs: dict[str, list] = {}
        opts: dict[str, dict[str, list]] = {}
        for x in rows:
            if x.get("segment") != "MCX_FO":
                continue
            sym = _mcx_symbol(x)
            exp = x.get("expiry") or 0
            if not sym or not exp:
                continue
            exp_d = datetime.fromtimestamp(exp / 1000, IST).date()
            if exp_d < today:
                continue
            itype = x.get("instrument_type")
            if itype == "FUT":
                futs.setdefault(sym, []).append((exp_d, x))
            elif itype in ("CE", "PE") and x.get("strike_price") is not None:
                opts.setdefault(sym, {}).setdefault(exp_d.isoformat(), []).append(
                    (float(x["strike_price"]), itype, x["instrument_key"], x.get("underlying_key") or ""))
        self.mcx_opts = opts
        roll_cut = today + timedelta(days=MCX_ROLL_DAYS)
        for sym, lst in futs.items():
            lst.sort(key=lambda t: t[0])
            exp_d, x = next((t for t in lst if t[0] >= roll_cut), lst[0])
            group, point_value, _, label = MCX[sym]
            self.meta[sym] = {
                "expiry": x.get("expiry"), "fut_key": x["instrument_key"], "eq_key": x["instrument_key"],
                "lot_size": point_value, "kind": "commodity",
                "expiries": sorted(opts.get(sym, {})),
                "contract": x.get("trading_symbol") or f"{sym} FUT", "fut_expiry": exp_d.isoformat(),
                "exchange_lot": x.get("lot_size"),
            }
            self.stocks[sym] = StockData(symbol=sym, name=label, sector=group, kind="commodity",
                                         lot_size=point_value)

    def _build_universe(self, rows: list[dict]):
        now_ms = time.time() * 1000
        names = {x.get("instrument_key"): x.get("name") for x in rows if x.get("segment") == "NSE_EQ"}
        index_keys = {v[4]: k for k, v in INDICES.items()}
        futs: dict[str, dict] = {}
        expiries: dict[str, set] = {}
        for x in rows:
            if x.get("segment") != "NSE_FO":
                continue
            itype = x.get("instrument_type")
            exp = x.get("expiry") or 0
            if exp and exp < now_ms - 86_400_000:
                continue
            utype = x.get("underlying_type")
            ukey = x.get("underlying_key") or x.get("asset_key")
            if utype == "EQUITY":
                sym = x.get("underlying_symbol") or x.get("asset_symbol")
            elif utype == "INDEX" and ukey in index_keys:
                sym = index_keys[ukey]
            else:
                continue
            if not sym or not ukey:
                continue
            if itype in ("CE", "PE") and exp:
                expiries.setdefault(sym, set()).add(datetime.fromtimestamp(exp / 1000, IST).date().isoformat())
            if itype != "FUT":
                continue
            cur = futs.get(sym)
            if cur is None or exp < cur["expiry"]:
                futs[sym] = {"expiry": exp, "fut_key": x["instrument_key"], "eq_key": ukey,
                             "lot_size": int(x.get("lot_size") or 0),
                             "kind": "index" if utype == "INDEX" else "stock"}
        self.meta = futs
        for sym, m in self.meta.items():
            m["expiries"] = sorted(expiries.get(sym, ()))
        self.stocks = {}
        for s_, m in self.meta.items():
            if m["kind"] == "index":
                name = INDICES[s_][0]
            else:
                name = (names.get(m["eq_key"]) or s_).title()
            self.stocks[s_] = StockData(symbol=s_, name=name, sector=sector_of(s_),
                                        kind=m["kind"], lot_size=m["lot_size"])

    # ------------------------------------------------------------- loading
    async def start(self):
        self.ready = False
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30)
        if not self.token:
            self.needs_login = True
            self.message = "Connect your Upstox account to load live data"
            return
        rows = await self._load_instruments()
        self._build_universe(rows)
        del rows
        try:
            self._build_mcx(await self._load_instruments("MCX"))
            self.mcx_error = "" if any(m["kind"] == "commodity" for m in self.meta.values()) \
                else "No MCX commodity futures found in the Upstox instrument list"
        except LoginRequired:
            raise
        except Exception as e:                       # MCX trouble must never stop the NSE board
            self.mcx_error = f"MCX list failed: {e}"
            print(f"[upstox] {self.mcx_error}")
        self.loaded_at = time.time()
        syms = sorted(self.stocks)
        self.progress = [0, len(syms)]
        n_com = sum(1 for m in self.meta.values() if m["kind"] == "commodity")
        self.message = f"Loading candles for {len(syms) - n_com} F&O stocks + {n_com} MCX commodities…"

        today = now_ist().date()
        hist_to = today - timedelta(days=1)
        hist_from = today - timedelta(days=HISTORY_DAYS)

        sem = asyncio.Semaphore(6)

        async def load(sym):
            async with sem:
                try:
                    await self._load_stock(sym, hist_from, hist_to)
                except LoginRequired:
                    raise
                except Exception as e:  # keep going; one bad symbol shouldn't stop the board
                    self.stocks[sym].candles = self.stocks[sym].candles or []
                    print(f"[upstox] {sym}: {e}")
                self.progress[0] += 1

        await asyncio.gather(*(load(s) for s in syms))
        days = [c.day for sd in self.stocks.values() if sd.kind != "commodity" for c in sd.candles[-1:]]
        self.session_day = max(days) if days else today
        try:
            await self._quotes()
        except LoginRequired:
            raise
        except Exception as e:
            print(f"[upstox] quotes: {e}")
        self.ready = True
        self.needs_login = False
        self.message = "Live data from Upstox"

    async def _load_stock(self, sym, hist_from, hist_to):
        m = self.meta[sym]
        sd = self.stocks[sym]
        hist = await self._candles(m["eq_key"], "minutes", 5, hist_to, hist_from)
        intra = await self._candles(m["eq_key"], "minutes", 5)
        merged = {c.t: c for c in hist}
        merged.update({c.t: c for c in intra})
        candles = sorted(merged.values(), key=lambda c: c.t)
        sessions = sorted({c.day for c in candles})[-KEEP_SESSIONS:]
        keep = set(sessions)
        sd.candles = [c for c in candles if c.day in keep]
        if sd.candles:
            last_day = sd.candles[-1].day
            prior = [c for c in sd.candles if c.day < last_day]
            sd.prev_close = prior[-1].c if prior else sd.candles[0].o
            sd.ltp = sd.candles[-1].c
            sd.day_volume = sum(c.v for c in sd.candles if c.day == last_day)
        self._last_full[sym] = time.time()

        # futures open interest: today's 30-min candles, else the last two daily bars
        fut = await self._candles(m["fut_key"], "minutes", 30)
        if fut:
            sd.oi_base = fut[0].oi
            sd.oi = fut[-1].oi
        else:
            daily = await self._candles(m["fut_key"], "days", 1, hist_to, hist_from)
            if len(daily) >= 2:
                sd.oi_base, sd.oi = daily[-2].oi, daily[-1].oi
        sd.updated = time.time()

    # ------------------------------------------------------------- live updates
    async def _quotes(self, kinds: set | None = None):
        """Full quotes for every instrument whose exchange is in `kinds` (None = all)."""
        keys, seen = [], set()
        for m in self.meta.values():
            if kinds is not None and ("commodity" if m["kind"] == "commodity" else "nse") not in kinds:
                continue
            for k in (m["eq_key"], m["fut_key"]):
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        keys += sorted(k for k in self.watch_keys if k not in seen)
        if not keys:
            return
        by_key = {}
        for i in range(0, len(keys), 250):
            js = await self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(keys[i:i + 250])})
            for v in (js.get("data") or {}).values():
                tok = v.get("instrument_token")
                if tok:
                    by_key[tok] = v
        now = time.time()
        live = {"stock": sessions.nse_open(), "commodity": sessions.mcx_open()}
        live["index"] = live["stock"]
        bucket = int(now // BAR_SECONDS * BAR_SECONDS)
        for sym, m in self.meta.items():
            sd = self.stocks[sym]
            q = by_key.get(m["eq_key"])
            if q is None:
                continue
            if q and q.get("last_price"):
                ltp = float(q["last_price"])
                sd.ltp = ltp
                if q.get("net_change") is not None:
                    sd.prev_close = round(ltp - float(q["net_change"]), 2)
                sd.day_volume = float(q.get("volume") or sd.day_volume)
                if live.get(sd.kind) and sd.candles:
                    self._update_forming(sd, bucket, ltp)
            fq = by_key.get(m["fut_key"])
            if fq and fq.get("oi"):
                sd.oi = float(fq["oi"])
            sd.updated = now
        for k in self.watch_keys:
            q = by_key.get(k)
            if q and q.get("last_price"):
                self.extra_ltp[k] = float(q["last_price"])
        self.last_quote_ok = now

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
        if not self.token or self.needs_login:
            return
        n = now_ist()
        nse, mcx = sessions.nse_open(n), sessions.mcx_open(n)
        if not (nse or mcx):
            return
        # first refresh after 09:15 on a new trading day: full reload (MCX runs on live
        # quotes between 09:00 and 09:15, then gets reloaded with everything else)
        reload_at = datetime(n.year, n.month, n.day, 9, 15, tzinfo=IST).timestamp()
        if nse and self.loaded_at and self.loaded_at < reload_at:
            await self.start()
            return
        kinds = ({"nse"} if nse else set()) | ({"commodity"} if mcx else set())
        await self._quotes(kinds)
        # re-download real candles for a few instruments whose exchange is open:
        # top-ranked first, then round-robin
        now = time.time()
        open_syms = {s for s, m in self.meta.items() if (mcx if m["kind"] == "commodity" else nse)}
        picks = [s for s in self.priority if s in open_syms and now - self._last_full.get(s, 0) > 300][:ROTATE_PER_CYCLE // 2]
        syms = sorted(open_syms)
        while len(picks) < min(ROTATE_PER_CYCLE, len(syms)):
            s = syms[self._rotate_pos % len(syms)]
            self._rotate_pos += 1
            if s not in picks:
                picks.append(s)
        for s in picks:
            try:
                intra = await self._candles(self.meta[s]["eq_key"], "minutes", 5)
            except LoginRequired:
                raise
            except Exception as e:
                print(f"[upstox] refresh {s}: {e}")
                continue
            if intra:
                sd = self.stocks[s]
                keep = [c for c in sd.candles if c.t < intra[0].t]
                sd.candles = keep + intra
                if sd.ltp:
                    self._update_forming(sd, int(now // BAR_SECONDS * BAR_SECONDS), sd.ltp)
                self._last_full[s] = now

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
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30)
        if m["kind"] == "commodity":
            chain = await self._mcx_chain(symbol, expiry)
            self._chain_cache[ck] = (time.time(), chain)
            return chain
        js = await self._get("/v2/option/chain", {"instrument_key": m["eq_key"], "expiry_date": expiry})
        rows, spot = [], 0.0
        for x in js.get("data") or []:
            spot = x.get("underlying_spot_price") or spot
            row = {"strike": float(x.get("strike_price") or 0)}
            for cp, field in (("CE", "call_options"), ("PE", "put_options")):
                o = x.get(field) or {}
                md = o.get("market_data") or {}
                gk = o.get("option_greeks") or {}
                row[cp] = {
                    "key": o.get("instrument_key"), "ltp": md.get("ltp") or 0, "oi": md.get("oi") or 0,
                    "prev_oi": md.get("prev_oi") or 0, "volume": md.get("volume") or 0,
                    "iv": gk.get("iv") or 0, "delta": gk.get("delta"), "gamma": gk.get("gamma"),
                    "theta": gk.get("theta") or 0,
                }
                if o.get("instrument_key") and md.get("ltp"):
                    self.extra_ltp[o["instrument_key"]] = float(md["ltp"])
            rows.append(row)
        chain = {"expiry": expiry, "spot": spot or self.stocks[symbol].ltp, "rows": rows}
        self._chain_cache[ck] = (time.time(), chain)
        return chain

    async def _mcx_chain(self, symbol: str, expiry: str) -> dict:
        """Upstox's option-chain API covers NSE/BSE only, so for MCX the chain is built from the
        instrument list + one quote call; IV and Greeks come from Black-76 on the future price."""
        contracts = (self.mcx_opts.get(symbol) or {}).get(expiry) or []
        sd = self.stocks[symbol]
        if not contracts:
            return {"expiry": expiry, "spot": sd.ltp, "rows": []}
        ukeys = {}
        for c in contracts:
            ukeys[c[3]] = ukeys.get(c[3], 0) + 1
        ukey = max(ukeys, key=ukeys.get) if ukeys else ""
        centre = sd.ltp
        strikes = sorted({c[0] for c in contracts}, key=lambda k: abs(k - centre))[:2 * MCX_CHAIN_STRIKES + 1]
        wanted = [c for c in contracts if c[0] in set(strikes)]
        keys = [c[2] for c in wanted] + ([ukey] if ukey and ukey != self.meta[symbol]["fut_key"] else [])
        js = await self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(keys)})
        by_key = {v.get("instrument_token"): v for v in (js.get("data") or {}).values() if v.get("instrument_token")}
        spot = sd.ltp
        if ukey and ukey in by_key and by_key[ukey].get("last_price"):
            spot = float(by_key[ukey]["last_price"])     # the future this option expiry settles on
        rows: dict[float, dict] = {}
        for strike, cp, key, _ in wanted:
            q = by_key.get(key) or {}
            ltp = float(q.get("last_price") or 0)
            rows.setdefault(strike, {"strike": strike})[cp] = {
                "key": key, "ltp": ltp, "oi": float(q.get("oi") or 0), "prev_oi": 0,
                "volume": float(q.get("volume") or 0), "iv": 0, "delta": None, "gamma": None, "theta": 0,
            }
            if ltp:
                self.extra_ltp[key] = ltp
        chain = {"expiry": expiry, "spot": spot, "rows": sorted(rows.values(), key=lambda r: r["strike"])}
        return fill_greeks(chain, r=0.0, close=sessions.mcx_close(date.fromisoformat(expiry)))

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
        return {
            "open": open_,
            "clock": n.strftime("%H:%M"),
            "session": self.session_day.isoformat() if self.session_day else "",
            "label": "Market open" if open_ else "Market closed - showing last session",
            "api_calls_30m": self.limiter.used(1800),
            "mcx": self.mcx_market(),
        }

    def mcx_market(self) -> dict:
        n = now_ist()
        open_ = sessions.mcx_open(n)
        h, mm = sessions.mcx_close(n.date())
        return {"open": open_, "hours": sessions.hours_label(n.date()), "close": f"{h:02d}:{mm:02d}",
                "label": "MCX open" if open_ else "MCX closed - showing last session",
                "error": self.mcx_error}


# ----------------------------------------------------------------- auth helpers

def login_url(api_key: str, redirect_uri: str) -> str:
    return (f"{API}/v2/login/authorization/dialog?response_type=code"
            f"&client_id={quote(api_key)}&redirect_uri={quote(redirect_uri, safe='')}")


async def exchange_code(code: str, api_key: str, api_secret: str, redirect_uri: str) -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/v2/login/authorization/token", data={
            "code": code, "client_id": api_key, "client_secret": api_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        }, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            raise RuntimeError(f"Upstox token exchange failed ({r.status_code}): {r.text[:300]}")
        return r.json()["access_token"]


def token_still_valid(saved_on: str) -> bool:
    """Upstox tokens expire at 3:30 AM IST the next day."""
    try:
        d = date.fromisoformat(saved_on[:10])
        t = datetime.fromisoformat(saved_on)
    except Exception:
        return False
    n = now_ist()
    expiry = datetime(d.year, d.month, d.day, 3, 30, tzinfo=IST)
    if t >= expiry:
        expiry += timedelta(days=1)
    return n < expiry
