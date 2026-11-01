"""
Dhan API v2.

Login: paste the Client ID and an Access Token generated on web.dhan.co
(My Profile -> Access DhanHQ APIs). Tokens are valid for 24 hours.
Needs Dhan's Data API subscription for charts, quotes and option chain.
Limits: historical 1/s, quotes 1/s (up to 1000 instruments), option chain 1 per 3 s.
"""
from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import date, datetime, timedelta

from ..models import IST, Candle, now_ist
from ..universe import INDICES
from .broker import BrokerError, BrokerProvider, Limiter, LoginRequired, chunks, parse_ts

API = "https://api.dhan.co/v2"
MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
INDEX_IDS = {"NIFTY": "13", "BANKNIFTY": "25"}


def _col(row: dict, *names, default=""):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return default


def parse_master(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for r in reader:
        r = {k.strip().upper(): (v or "").strip() for k, v in r.items() if k}
        out.append({
            "exch": _col(r, "SEM_EXM_EXCH_ID", "EXCH_ID"),
            "seg": _col(r, "SEM_SEGMENT", "SEGMENT"),
            "id": _col(r, "SEM_SMST_SECURITY_ID", "SECURITY_ID"),
            "inst": _col(r, "SEM_INSTRUMENT_NAME", "INSTRUMENT").upper(),
            "sym": _col(r, "SEM_TRADING_SYMBOL", "SYMBOL_NAME"),
            "lot": _col(r, "SEM_LOT_UNITS", "LOT_SIZE", default="0"),
            "expiry": _col(r, "SEM_EXPIRY_DATE", "SM_EXPIRY_DATE"),
            "series": _col(r, "SEM_SERIES", "SERIES").upper(),
            "under": _col(r, "UNDERLYING_SYMBOL"),
            "name": _col(r, "SM_SYMBOL_NAME", "DISPLAY_NAME"),
        })
    return out


def _exp_date(s: str):
    s = (s or "").strip()
    if not s or s.startswith("-1"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


class DhanProvider(BrokerProvider):
    key = "dhan"
    label = "Dhan"
    LIMITS = [(1, 1), (60, 55)]          # historical charts: 1 per second
    LOAD_CONCURRENCY = 1
    ROTATE_PER_CYCLE = 8
    CHAIN_TTL = 30

    def __init__(self, data_dir, creds):
        super().__init__(data_dir, creds)
        self.quote_limiter = Limiter([(1, 1)])
        self.chain_limiter = Limiter([(3, 1)])

    def has_token(self):
        return bool(self.creds.get("token") and self.creds.get("client_id"))

    def _headers(self):
        return {"access-token": self.creds.get("token", ""), "client-id": self.creds.get("client_id", ""),
                "Content-Type": "application/json", "Accept": "application/json"}

    async def _post(self, path: str, body: dict, limiter: Limiter | None = None):
        await (limiter or self.limiter).wait()
        r = await self._client().post(API + path, json=body, headers=self._headers())
        try:
            js = r.json()
        except Exception:
            js = {}
        code = str(js.get("errorCode") or js.get("error_code") or "")
        if r.status_code == 401 or code in ("DH-901", "DH-902", "DH-808", "DH-809"):
            self.needs_login = True
            raise LoginRequired()
        if r.status_code == 429 or code == "DH-904":
            await asyncio.sleep(2)
            raise BrokerError("Dhan rate limit reached")
        if r.status_code >= 400 or js.get("status") == "failure":
            msg = js.get("errorMessage") or js.get("remarks") or r.text[:200]
            if "subscri" in str(msg).lower() or code == "DH-902":
                raise BrokerError("Dhan says your Data API subscription is not active")
            raise BrokerError(f"Dhan {path}: {msg}")
        return js

    # ------------------------------------------------------------- universe
    async def _universe(self):
        text = (await self._download_cached(MASTER, "scrip-master.csv")).decode("utf-8", "replace")
        rows = parse_master(text)
        today = now_ist().date()
        eq: dict[str, dict] = {}
        futs: dict[str, dict] = {}
        exps: dict[str, set] = {}
        for x in rows:
            if x["exch"] != "NSE":
                continue
            inst = x["inst"]
            if inst == "EQUITY" and x["series"] in ("EQ", ""):
                eq.setdefault(x["sym"], x)
                continue
            if inst not in ("FUTSTK", "FUTIDX", "OPTSTK", "OPTIDX"):
                continue
            under = x["under"] or x["sym"].split("-")[0].split(" ")[0]
            if inst in ("FUTIDX", "OPTIDX") and under not in INDICES:
                continue
            d = _exp_date(x["expiry"])
            if not d or d < today:
                continue
            if inst.startswith("OPT"):
                exps.setdefault(under, set()).add(d.isoformat())
                continue
            cur = futs.get(under)
            if cur is None or d < cur["exp"]:
                futs[under] = {"exp": d, "fut": x["id"], "lot": int(float(x["lot"] or 0)),
                               "kind": "index" if inst == "FUTIDX" else "stock"}
        meta = {}
        for u, f in futs.items():
            if f["kind"] == "index":
                spot_id, seg, inst = INDEX_IDS[u], "IDX_I", "INDEX"
            else:
                e = eq.get(u)
                if not e:
                    continue
                spot_id, seg, inst = e["id"], "NSE_EQ", "EQUITY"
            meta[u] = {"kind": f["kind"], "lot": f["lot"], "fut": f["fut"], "spot": spot_id, "seg": seg,
                       "inst": inst, "fut_inst": "FUTIDX" if f["kind"] == "index" else "FUTSTK",
                       "expiries": sorted(exps.get(u, ())), "name": (eq.get(u) or {}).get("name") or u}
        return meta

    # ------------------------------------------------------------- candles
    async def _intraday(self, sec_id, seg, inst, d0: date, d1: date, oi=False) -> list[Candle]:
        js = await self._post("/charts/intraday", {
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst, "interval": "5", "oi": oi,
            "fromDate": f"{d0.isoformat()} 09:00:00", "toDate": f"{d1.isoformat()} 15:30:00"})
        d = js.get("data") if isinstance(js.get("data"), dict) else js
        ts = d.get("timestamp") or []
        o, h, l, c = d.get("open") or [], d.get("high") or [], d.get("low") or [], d.get("close") or []
        v, ois = d.get("volume") or [], d.get("open_interest") or []
        out = []
        for i, t in enumerate(ts):
            try:
                out.append(Candle(t=parse_ts(t), o=float(o[i]), h=float(h[i]), l=float(l[i]), c=float(c[i]),
                                  v=float(v[i]) if i < len(v) else 0.0, oi=float(ois[i]) if i < len(ois) else 0.0))
            except (IndexError, TypeError, ValueError):
                continue
        return out

    async def _history(self, sym, m, d0, d1):
        return await self._intraday(m["spot"], m["seg"], m["inst"], d0, d1)

    async def _fut_oi(self, sym, m):
        today = now_ist().date()
        cs = [c for c in await self._intraday(m["fut"], "NSE_FNO", m["fut_inst"], today - timedelta(days=6), today, oi=True)
              if c.oi]
        if not cs:
            return 0.0, 0.0
        prev = [c for c in cs if c.day < cs[-1].day]
        return (prev[-1].oi if prev else cs[0].oi), cs[-1].oi

    # ------------------------------------------------------------- quotes
    async def _quotes(self, syms, extra):
        req: dict[str, list[int]] = {}
        back: dict[tuple, tuple] = {}
        for s in syms:
            m = self.meta[s]
            req.setdefault(m["seg"], []).append(int(m["spot"]))
            back[(m["seg"], str(m["spot"]))] = (s, "spot")
            req.setdefault("NSE_FNO", []).append(int(m["fut"]))
            back[("NSE_FNO", str(m["fut"]))] = (s, "fut")
        for k in extra:                      # option keys look like "NSE_FNO|12345"
            seg, _, sid = k.partition("|")
            if sid.isdigit():
                req.setdefault(seg, []).append(int(sid))
                back[(seg, sid)] = (k, "extra")
        flat = [(seg, i) for seg, ids in req.items() for i in ids]
        data, ext = {}, {}
        for part in chunks(flat, 1000):
            body: dict[str, list[int]] = {}
            for seg, i in part:
                body.setdefault(seg, []).append(i)
            js = await self._post("/marketfeed/quote", body, self.quote_limiter)
            for seg, items in (js.get("data") or {}).items():
                for sid, q in (items or {}).items():
                    who = back.get((seg, str(sid)))
                    if not who or not isinstance(q, dict):
                        continue
                    name, kind = who
                    lp = q.get("last_price")
                    if kind == "extra":
                        if lp:
                            ext[name] = float(lp)
                    elif kind == "spot" and lp:
                        prev = (lp - q["net_change"]) if q.get("net_change") is not None else (q.get("ohlc") or {}).get("close")
                        data.setdefault(name, {}).update(ltp=lp, prev_close=prev, volume=q.get("volume"))
                    elif kind == "fut" and q.get("oi"):
                        data.setdefault(name, {})["oi"] = q["oi"]
        return data, ext

    # ------------------------------------------------------------- option chain
    async def _chain(self, sym, m, expiry):
        js = await self._post("/optionchain", {"UnderlyingScrip": int(m["spot"]), "UnderlyingSeg": m["seg"],
                                               "Expiry": expiry}, self.chain_limiter)
        d = js.get("data") or {}
        rows = []
        for k, v in (d.get("oc") or {}).items():
            try:
                strike = float(k)
            except ValueError:
                continue
            row = {"strike": strike}
            for cp, side in (("CE", "ce"), ("PE", "pe")):
                o = (v or {}).get(side)
                if not o:
                    continue
                g = o.get("greeks") or {}
                row[cp] = {"key": f"NSE_FNO|{o.get('security_id')}" if o.get("security_id") else None,
                           "ltp": o.get("last_price") or 0, "oi": o.get("oi") or 0,
                           "prev_oi": o.get("previous_oi") or 0, "volume": o.get("volume") or 0,
                           "iv": o.get("implied_volatility") or 0, "delta": g.get("delta"),
                           "gamma": g.get("gamma"), "theta": g.get("theta") or 0}
            rows.append(row)
        return {"expiry": expiry, "spot": d.get("last_price") or 0, "rows": rows}
