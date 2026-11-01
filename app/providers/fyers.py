"""
Fyers API v3.

Login: browser redirect to generate-authcode -> auth_code -> validate-authcode
(appIdHash = sha256("APP_ID:SECRET")). Token resets every morning.
Data:  /data/history (5-min, up to 100 days per call, oi_flag for futures),
       /data/quotes (50 symbols per call), /data/options-chain-v3 (with Greeks).
Instruments: public symbol master CSV (no login needed).
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from ..models import IST, Candle, now_ist
from ..universe import INDICES
from .broker import BrokerError, BrokerProvider, LoginRequired, chunks, parse_ts

API = "https://api-t1.fyers.in/api/v3"
DATA = "https://api-t1.fyers.in/data"
FO_MASTER = "https://public.fyers.in/sym_details/NSE_FO.csv"
INDEX_SYMBOL = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX"}
FUTIDX, FUTSTK, OPTIDX, OPTSTK = 11, 13, 14, 15
AUTH_FAIL_CODES = {-8, -15, -16, -17}


def login_url(app_id: str, redirect_uri: str, state: str = "blockradar") -> str:
    return f"{API}/generate-authcode?" + urlencode(
        {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state})


async def exchange_code(client, app_id: str, secret: str, auth_code: str) -> str:
    h = hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()
    r = await client.post(f"{API}/validate-authcode",
                          json={"grant_type": "authorization_code", "appIdHash": h, "code": auth_code})
    js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or js.get("s") != "ok" or not js.get("access_token"):
        raise RuntimeError(f"Fyers token exchange failed ({r.status_code}): {r.text[:300]}")
    return js["access_token"]


def parse_master(text: str) -> list[dict]:
    """
    Fyers NSE_FO.csv has no header. Documented column order:
    0 fytoken, 1 symbol details, 2 instrument type, 3 lot size, 4 tick size, 5 isin,
    6 session, 7 last update, 8 expiry epoch, 9 symbol ticker, 10 exchange, 11 segment,
    12 scrip code, 13 underlying symbol, 14 underlying scrip code, 15 strike, 16 option type ...
    The ticker column is located by content in case the order ever shifts.
    """
    rows = []
    reader = csv.reader(io.StringIO(text))
    tick_i = None
    for r in reader:
        if len(r) < 17:
            continue
        if tick_i is None:
            if not r[0].strip().isdigit():          # header line
                continue
            tick_i = 9 if r[9].startswith("NSE:") else next(
                (i for i, v in enumerate(r) if v.startswith("NSE:")), None)
            if tick_i is None:
                raise BrokerError("Unrecognised Fyers symbol master format")
        try:
            rows.append({
                "type": int(float(r[2])), "lot": int(float(r[3] or 0)), "expiry": int(float(r[8] or 0)),
                "ticker": r[tick_i], "under": r[tick_i + 4].strip(), "strike": float(r[tick_i + 6] or 0),
                "opt": r[tick_i + 7].strip(),
            })
        except (ValueError, IndexError):
            continue
    return rows


class FyersProvider(BrokerProvider):
    key = "fyers"
    label = "Fyers"
    # Fyers: ~10/s, 200/min; daily cap reported as 10,000-100,000 -> stay well under all of them
    LIMITS = [(1, 6), (60, 150), (86400, 9000)]
    ROTATE_PER_CYCLE = 6
    OI_FROM_QUOTES = False      # Fyers quotes carry no OI

    def _headers(self):
        return {"Authorization": f"{self.creds.get('app_id')}:{self.creds.get('token')}"}

    async def _get(self, url: str, params: dict):
        await self.limiter.wait()
        r = await self._client().get(url, params=params, headers=self._headers())
        if r.status_code in (401, 403):
            self.needs_login = True
            raise LoginRequired()
        if r.status_code == 429:
            raise BrokerError("Fyers rate limit reached (429)")
        try:
            js = r.json()
        except Exception:
            raise BrokerError(f"Fyers {url} returned {r.status_code}: {r.text[:200]}")
        if js.get("s") != "ok":
            if js.get("code") in AUTH_FAIL_CODES:
                self.needs_login = True
                raise LoginRequired()
            raise BrokerError(f"Fyers: {js.get('message') or js}")
        return js

    # ------------------------------------------------------------- universe
    async def _universe(self):
        text = (await self._download_cached(FO_MASTER, "NSE_FO.csv")).decode("utf-8", "replace")
        rows = parse_master(text)
        now = now_ist().timestamp() - 86400
        futs, exps = {}, {}
        for x in rows:
            u = x["under"]
            if x["type"] == FUTIDX and u not in INDICES:
                continue
            if x["type"] in (OPTIDX, OPTSTK) and x["expiry"] > now:
                if x["type"] == OPTIDX and u not in INDICES:
                    continue
                d = datetime.fromtimestamp(x["expiry"], IST).date().isoformat()
                exps.setdefault(u, {})[d] = x["expiry"]
            if x["type"] in (FUTIDX, FUTSTK) and x["expiry"] > now:
                cur = futs.get(u)
                if cur is None or x["expiry"] < cur["exp"]:
                    futs[u] = {"exp": x["expiry"], "fut": x["ticker"], "lot": x["lot"],
                               "kind": "index" if x["type"] == FUTIDX else "stock"}
        meta = {}
        for u, f in futs.items():
            spot = INDEX_SYMBOL[u] if f["kind"] == "index" else f"NSE:{u}-EQ"
            e = exps.get(u, {})
            meta[u] = {**f, "spot": spot, "expiries": sorted(e), "exp_epoch": e, "name": u}
        return meta

    # ------------------------------------------------------------- candles
    async def _hist(self, symbol: str, d0: date, d1: date, oi: bool = False) -> list[Candle]:
        params = {"symbol": symbol, "resolution": "5", "date_format": "1",
                  "range_from": d0.isoformat(), "range_to": d1.isoformat(), "cont_flag": "1"}
        if oi:
            params["oi_flag"] = "1"
        js = await self._get(f"{DATA}/history", params)
        out = []
        for r in js.get("candles") or []:
            out.append(Candle(t=parse_ts(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                              v=float(r[5] or 0), oi=float(r[6]) if len(r) > 6 and r[6] is not None else 0.0))
        return out

    async def _history(self, sym, m, d0, d1):
        return await self._hist(m["spot"], d0, d1)

    async def _fut_oi(self, sym, m):
        today = now_ist().date()
        cs = await self._hist(m["fut"], today - timedelta(days=6), today, oi=True)
        cs = [c for c in cs if c.oi]
        if not cs:
            return 0.0, 0.0
        last_day = cs[-1].day
        prev = [c for c in cs if c.day < last_day]
        base = prev[-1].oi if prev else cs[0].oi
        return base, cs[-1].oi

    # ------------------------------------------------------------- quotes
    async def _quotes(self, syms, extra):
        by_symbol = {self.meta[s]["spot"]: s for s in syms}
        wanted = list(by_symbol) + [k for k in extra if k not in by_symbol]
        data, ext = {}, {}
        for part in chunks(wanted, 50):
            js = await self._get(f"{DATA}/quotes", {"symbols": ",".join(part)})
            for item in js.get("d") or []:
                v = item.get("v") or {}
                name = item.get("n") or v.get("symbol")
                lp = v.get("lp")
                if not name or lp is None:
                    continue
                if name in by_symbol:
                    prev = v.get("prev_close_price") or (lp - (v.get("ch") or 0))
                    data[by_symbol[name]] = {"ltp": lp, "prev_close": prev, "volume": v.get("volume")}
                else:
                    ext[name] = float(lp)
        return data, ext

    # ------------------------------------------------------------- option chain
    async def _chain(self, sym, m, expiry):
        params = {"symbol": m["spot"], "strikecount": "15", "greeks": "1"}
        ep = (m.get("exp_epoch") or {}).get(expiry)
        if ep:
            params["timestamp"] = str(ep)
        js = await self._get(f"{DATA}/options-chain-v3", params)
        d = js.get("data") or {}
        if not ep:   # fall back to the broker's own expiry list
            for e in d.get("expiryData") or []:
                try:
                    dd = datetime.strptime(e["date"], "%d-%m-%Y").date().isoformat()
                except Exception:
                    continue
                if dd == expiry:
                    params["timestamp"] = str(e["expiry"])
                    d = (await self._get(f"{DATA}/options-chain-v3", params)).get("data") or {}
                    break
        rows: dict[float, dict] = {}
        spot = 0.0
        for o in d.get("optionsChain") or []:
            k = o.get("strike_price")
            if k is None or k < 0:
                spot = o.get("ltp") or o.get("fp") or spot
                continue
            cp = o.get("option_type")
            if cp not in ("CE", "PE"):
                continue
            g = o.get("greeks") or {}
            rows.setdefault(float(k), {"strike": float(k)})[cp] = {
                "key": o.get("symbol"), "ltp": o.get("ltp") or 0, "oi": o.get("oi") or 0,
                "prev_oi": o.get("prev_oi") or 0, "volume": o.get("volume") or 0,
                "iv": g.get("iv") or 0, "delta": g.get("delta"), "gamma": g.get("gamma"), "theta": g.get("theta") or 0,
            }
        return {"expiry": expiry, "spot": spot, "rows": list(rows.values())}
