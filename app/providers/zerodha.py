"""
Zerodha Kite Connect v3.

Login: browser redirect to kite.zerodha.com/connect/login -> request_token ->
POST /session/token with checksum sha256(api_key + request_token + api_secret).
Needs the paid Kite Connect plan (the free "Personal" plan has no market data).
Data: /instruments (CSV), /instruments/historical (5minute, oi=1 for futures),
/quote (up to 500 instruments). There is no option-chain endpoint, so the
chain is assembled from the instrument list + one quote call, with IV and
Greeks solved locally.
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, timedelta
from urllib.parse import urlencode

from ..models import Candle, now_ist
from ..universe import INDICES
from .broker import BrokerError, BrokerProvider, Limiter, LoginRequired, chunks, parse_ts

API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"
INDEX_SPOT = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}
STRIKES_EACH_SIDE = 12


def login_url(api_key: str) -> str:
    return f"{LOGIN}?" + urlencode({"v": "3", "api_key": api_key})


async def exchange_code(client, api_key: str, api_secret: str, request_token: str) -> str:
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
    r = await client.post(f"{API}/session/token", data={"api_key": api_key, "request_token": request_token,
                                                        "checksum": checksum},
                          headers={"X-Kite-Version": "3"})
    try:
        js = r.json()
    except Exception:
        js = {}
    if r.status_code != 200 or not (js.get("data") or {}).get("access_token"):
        raise RuntimeError(f"Zerodha token exchange failed ({r.status_code}): {r.text[:300]}")
    return js["data"]["access_token"]


class ZerodhaProvider(BrokerProvider):
    key = "zerodha"
    label = "Zerodha"
    LIMITS = [(1, 3), (60, 170)]          # historical: 3 per second
    LOAD_CONCURRENCY = 3
    ROTATE_PER_CYCLE = 10

    def __init__(self, data_dir, creds):
        super().__init__(data_dir, creds)
        self.quote_limiter = Limiter([(1, 1)])
        self.options: dict[str, dict[str, list]] = {}    # symbol -> expiry -> [(strike, CE/PE, tradingsymbol)]

    def has_token(self):
        return bool(self.creds.get("token") and self.creds.get("api_key"))

    def _headers(self):
        return {"X-Kite-Version": "3", "Authorization": f"token {self.creds.get('api_key')}:{self.creds.get('token')}"}

    async def _get(self, path: str, params=None, limiter: Limiter | None = None, raw=False):
        await (limiter or self.limiter).wait()
        r = await self._client().get(API + path, params=params, headers=self._headers())
        if r.status_code in (401, 403):
            self.needs_login = True
            raise LoginRequired()
        if r.status_code == 429:
            raise BrokerError("Zerodha rate limit reached (429)")
        if raw:
            r.raise_for_status()
            return r.text
        try:
            js = r.json()
        except Exception:
            raise BrokerError(f"Zerodha {path} returned {r.status_code}: {r.text[:200]}")
        if js.get("status") != "success":
            if js.get("error_type") in ("TokenException", "PermissionException"):
                self.needs_login = js.get("error_type") == "TokenException"
                if self.needs_login:
                    raise LoginRequired()
                raise BrokerError("Zerodha: your app has no market-data permission (needs the paid Kite Connect plan)")
            raise BrokerError(f"Zerodha: {js.get('message') or js}")
        return js.get("data")

    async def _csv(self, exchange: str) -> list[dict]:
        path = self.data_dir / f"zerodha_{exchange}_{date.today():%Y%m%d}.csv"
        if not path.exists():
            self.message = "Downloading Zerodha instrument list…"
            text = await self._get(f"/instruments/{exchange}", raw=True)
            for old in self.data_dir.glob(f"zerodha_{exchange}_*.csv"):
                old.unlink(missing_ok=True)
            path.write_text(text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))

    # ------------------------------------------------------------- universe
    async def _universe(self):
        nfo = await self._csv("NFO")
        nse = await self._csv("NSE")
        today = now_ist().date().isoformat()
        spot_tok = {}
        for x in nse:
            if x.get("segment") == "INDICES" or x.get("instrument_type") == "EQ":
                spot_tok[x["tradingsymbol"]] = x
        futs, self.options = {}, {}
        for x in nfo:
            name, exp = x.get("name", "").strip('"'), x.get("expiry", "")
            if not exp or exp < today:
                continue
            itype = x.get("instrument_type")
            seg = x.get("segment")
            if name in ("FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"):
                continue
            if itype in ("CE", "PE"):
                self.options.setdefault(name, {}).setdefault(exp, []).append(
                    (float(x.get("strike") or 0), itype, x["tradingsymbol"]))
            elif itype == "FUT" and seg == "NFO-FUT":
                cur = futs.get(name)
                if cur is None or exp < cur["exp"]:
                    futs[name] = {"exp": exp, "fut": x["instrument_token"], "fut_sym": x["tradingsymbol"],
                                  "lot": int(float(x.get("lot_size") or 0))}
        meta = {}
        for name, f in futs.items():
            kind = "index" if name in INDICES else "stock"
            spot_sym = INDEX_SPOT.get(name, name)
            s = spot_tok.get(spot_sym)
            if not s:
                continue
            meta[name] = {**f, "kind": kind, "spot": s["instrument_token"], "spot_q": f"NSE:{spot_sym}",
                          "expiries": sorted(self.options.get(name, {})), "name": name}
        return meta

    # ------------------------------------------------------------- candles
    async def _hist(self, token, d0: date, d1: date, oi=False) -> list[Candle]:
        data = await self._get(f"/instruments/historical/{token}/5minute", {
            "from": f"{d0.isoformat()} 09:00:00", "to": f"{d1.isoformat()} 15:30:00", "oi": 1 if oi else 0})
        out = []
        for r in (data or {}).get("candles") or []:
            out.append(Candle(t=parse_ts(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                              v=float(r[5] or 0), oi=float(r[6]) if len(r) > 6 and r[6] is not None else 0.0))
        return out

    async def _history(self, sym, m, d0, d1):
        return await self._hist(m["spot"], d0, d1)

    async def _fut_oi(self, sym, m):
        today = now_ist().date()
        cs = [c for c in await self._hist(m["fut"], today - timedelta(days=6), today, oi=True) if c.oi]
        if not cs:
            return 0.0, 0.0
        prev = [c for c in cs if c.day < cs[-1].day]
        return (prev[-1].oi if prev else cs[0].oi), cs[-1].oi

    # ------------------------------------------------------------- quotes
    async def _quote(self, keys: list[str]) -> dict:
        out = {}
        for part in chunks(keys, 500):
            data = await self._get("/quote", [("i", k) for k in part], self.quote_limiter)
            out.update(data or {})
        return out

    async def _quotes(self, syms, extra):
        back = {}
        for s in syms:
            m = self.meta[s]
            back[m["spot_q"]] = (s, "spot")
            back[f"NFO:{m['fut_sym']}"] = (s, "fut")
        for k in extra:
            back.setdefault(k, (k, "extra"))
        q = await self._quote(list(back))
        data, ext = {}, {}
        for k, v in q.items():
            who = back.get(k)
            if not who:
                continue
            name, kind = who
            lp = v.get("last_price")
            if kind == "extra":
                if lp:
                    ext[name] = float(lp)
            elif kind == "spot" and lp:
                prev = (v.get("ohlc") or {}).get("close") or (lp - (v.get("net_change") or 0))
                data.setdefault(name, {}).update(ltp=lp, prev_close=prev, volume=v.get("volume"))
            elif kind == "fut" and v.get("oi"):
                data.setdefault(name, {})["oi"] = v["oi"]
        return data, ext

    # ------------------------------------------------------------- option chain
    async def _chain(self, sym, m, expiry):
        contracts = (self.options.get(sym) or {}).get(expiry) or []
        if not contracts:
            return {"expiry": expiry, "spot": 0, "rows": []}
        spot = self.stocks[sym].ltp
        strikes = sorted({k for k, _, _ in contracts})
        atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        keep = set(strikes[max(0, atm - STRIKES_EACH_SIDE): atm + STRIKES_EACH_SIDE + 1])
        keys = [f"NFO:{ts}" for k, _, ts in contracts if k in keep]
        q = await self._quote(keys + [m["spot_q"]])
        spot = (q.get(m["spot_q"]) or {}).get("last_price") or spot
        rows: dict[float, dict] = {}
        for k, cp, ts in contracts:
            if k not in keep:
                continue
            v = q.get(f"NFO:{ts}") or {}
            rows.setdefault(k, {"strike": k})[cp] = {
                "key": f"NFO:{ts}", "ltp": v.get("last_price") or 0, "oi": v.get("oi") or 0,
                "prev_oi": 0, "volume": v.get("volume") or 0, "iv": 0, "delta": None, "gamma": None, "theta": 0}
        return {"expiry": expiry, "spot": spot, "rows": list(rows.values())}
