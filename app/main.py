from __future__ import annotations

import asyncio
import json
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import html
import secrets
from urllib.parse import urlencode, urlparse

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .alerts import DEFAULTS as ALERT_DEFAULTS
from .alerts import Alerts
from .engine import analyze
from .journal import Journal
from .models import IST, aggregate, now_ist
from .options import build_helper, underlying_plan
from .paper import PaperBook
from .providers.demo import DemoProvider
from .brokers import BROKERS, make as make_broker, public_info, token_ok
from .providers import fyers as fyers_mod
from .providers import zerodha as zerodha_mod
from .providers.broker import LoginRequired
from .providers.upstox import exchange_code, login_url

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
DATA.mkdir(exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"

DEFAULT_SETTINGS = {
    "mode": "demo",                 # "demo" or a broker key
    "picked": False,                # has the first-launch broker picker been answered?
    "brokers": {k: {} for k in BROKERS},
    "alerts": dict(ALERT_DEFAULTS),
    "paper_eod": True,
}
LEGACY_UPSTOX = ("api_key", "api_secret", "redirect_uri", "token", "token_saved")


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            for k, v in saved.items():
                if k == "alerts":
                    s["alerts"].update(v or {})
                elif k == "brokers":
                    for b, c in (v or {}).items():
                        s["brokers"].setdefault(b, {}).update(c or {})
                elif k not in LEGACY_UPSTOX:
                    s[k] = v
            # settings written by the first version kept Upstox keys at the top level
            if not s["brokers"]["upstox"].get("api_key") and saved.get("api_key"):
                s["brokers"]["upstox"].update({k: saved[k] for k in LEGACY_UPSTOX if saved.get(k)})
            if "picked" not in saved:
                s["picked"] = saved.get("mode", "demo") != "demo" or bool(saved.get("api_key"))
        except Exception:
            pass
    if s["mode"] not in BROKERS and s["mode"] != "demo":
        s["mode"] = "demo"
    return s


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


alerts = Alerts()
paper = PaperBook(DATA / "paper.db")
journal = Journal(DATA / "journal.db")


# --------------------------------------------------------------------- scanner

class Scanner:
    def __init__(self):
        self.provider = None
        self.results: dict[str, dict] = {}
        self.updated = 0.0
        self.error = ""
        self.task: asyncio.Task | None = None

    async def switch(self, provider):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass
        if self.provider:
            await self.provider.stop()
        self.provider = provider
        self.results = {}
        self.updated = 0.0
        self.error = ""
        alerts.reset()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        p = self.provider
        try:
            await p.start()
        except LoginRequired:
            p.needs_login = True
            p.message = "Upstox login needed (tokens expire every morning at 3:30 AM)"
        except Exception as e:
            traceback.print_exc()
            self.error = f"Start-up failed: {e}"
        while True:
            if p.ready:
                try:
                    await self._analyze()
                    self._paper_tick()
                except Exception as e:
                    traceback.print_exc()
                    self.error = f"Analysis error: {e}"
            await asyncio.sleep(p.refresh_seconds)
            try:
                p.watch_keys = paper.watch_keys()
                await p.refresh()
                self.error = ""
            except LoginRequired:
                p.needs_login = True
                p.message = "Upstox session expired - reconnect from Settings"
            except Exception as e:
                traceback.print_exc()
                self.error = f"Data refresh failed: {e}"

    async def _analyze(self):
        stocks = list(self.provider.stocks.values())

        def work():
            out = {}
            for sd in stocks:
                try:
                    r = analyze(sd)
                except Exception:
                    traceback.print_exc()
                    r = None
                if r:
                    out[sd.symbol] = r
            return out

        self.results = await asyncio.to_thread(work)
        self.updated = time.time()
        bull, bear = self.ranked("bullish", 10), self.ranked("bearish", 10)
        self.provider.priority = [r["symbol"] for r in bull + bear]
        alerts.configure(load_settings()["alerts"])
        alerts.evaluate(self.results, {"bullish": [r["symbol"] for r in bull],
                                       "bearish": [r["symbol"] for r in bear]})

    def _paper_tick(self):
        p = self.provider
        m = p.market()
        cfg = load_settings()
        events = paper.update(
            lambda s: (p.stocks[s].ltp if s in p.stocks else None),
            p.option_price, m.get("clock", ""), m.get("open", False), cfg.get("paper_eod", True))
        if not cfg["alerts"].get("paper", True):
            return
        for ev in events:
            r, e = ev["row"], ev["event"]
            if e == "FILLED":
                text = f"Filled at {r['fill_price']} · stop {r['u_stop']} / target {r['u_target']} on {r['symbol']}"
            elif e == "CANCELLED":
                text = "Order cancelled (zone broke or market closing)"
            else:
                text = f"Closed at {r.get('exit_price')} · P&L ₹{r.get('pnl')}"
            word = {"FILLED": "filled", "TARGET": "hit TARGET", "STOP": "hit STOP", "EOD": "squared off",
                    "CANCELLED": "cancelled"}[e]
            alerts.add(r["symbol"], f"paper{r['id']}{e}", f"Paper: {r['label']} {word}", text,
                       1 if e in ("TARGET", "FILLED") else -1 if e == "STOP" else 0, force=True)

    def ranked(self, bias: str, limit: int):
        rows = [r for r in self.results.values()
                if r["bias"] == bias and r["score"] > 0 and r.get("kind") != "index"]
        rows.sort(key=lambda r: (-r["score"], -abs(r["change_pct"])))
        return rows[:limit]


scanner = Scanner()


def make_provider(settings: dict):
    mode = settings.get("mode")
    if mode in BROKERS and BROKERS[mode]["status"] != "unsupported":
        p = make_broker(mode, DATA, settings["brokers"].get(mode, {}))
        if p:
            return p
    return DemoProvider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await scanner.switch(make_provider(load_settings()))
    yield
    if scanner.task:
        scanner.task.cancel()
    if scanner.provider:
        await scanner.provider.stop()


app = FastAPI(title="BlockRadar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# --- security ---------------------------------------------------------------
# 1. Only answer requests addressed to this machine (blocks DNS-rebinding attacks).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """
    2. Requests that change anything must come from BlockRadar's own page:
       they need the X-BlockRadar header (other websites can't add custom headers
       without a CORS pre-flight, which this server never approves) and, if the
       browser sends an Origin, it must be this machine.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        if request.headers.get("x-blockradar") != "1":
            return JSONResponse({"detail": "Missing X-BlockRadar header"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


# 3. OAuth "state": a one-time random value checked on the way back from the broker,
#    so nobody can trick the app into logging into a different account.
_oauth_state: dict[str, tuple[str, float]] = {}


def _new_state(broker: str) -> str:
    st = secrets.token_urlsafe(24)
    _oauth_state[broker] = (st, time.time())
    return st


def _check_state(broker: str, got: str) -> bool:
    st, t = _oauth_state.pop(broker, ("", 0))
    return bool(got) and secrets.compare_digest(st, got) and time.time() - t < 900
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC / "index.html")


# --------------------------------------------------------------------- data API

CARD_KEYS = ("symbol", "name", "sector", "kind", "ltp", "change_pct", "norm_move", "score", "bias", "zone",
             "in_zone", "support", "resistance", "stop_liquidity", "target_liquidity", "oi", "trap",
             "rvol", "efficiency", "vwap", "lot_size", "mtf", "htf_confluence", "has_volume")


def card(r: dict) -> dict:
    return {k: r.get(k) for k in CARD_KEYS}


@app.get("/api/status")
async def status():
    p = scanner.provider
    s = load_settings()
    return {
        "mode": p.key if p else None,
        "mode_label": p.label if p else "",
        "ready": bool(p and p.ready),
        "message": p.message if p else "",
        "progress": p.progress if p else [0, 0],
        "needs_login": bool(p and p.needs_login),
        "has_keys": bool(s["mode"] in BROKERS and public_info(s["mode"], s["brokers"].get(s["mode"], {}))["configured"]),
        "broker": s["mode"],
        "broker_name": BROKERS[s["mode"]]["name"] if s["mode"] in BROKERS else "Demo",
        "broker_auth": BROKERS[s["mode"]]["auth"] if s["mode"] in BROKERS else None,
        "picked": s.get("picked", False),
        "market": p.market() if p else {},
        "updated": scanner.updated,
        "updated_label": datetime.fromtimestamp(scanner.updated, IST).strftime("%H:%M:%S") if scanner.updated else "",
        "error": scanner.error,
        "refresh_seconds": p.refresh_seconds if p else 60,
        "count": len(scanner.results),
        "last_alert": alerts.next_id - 1,
        "paper_active": len(paper.active()),
    }


@app.get("/api/board")
async def board(limit: int = 20):
    res = [r for r in scanner.results.values() if r.get("kind") != "index"]
    idx = [card(r) for r in scanner.results.values() if r.get("kind") == "index"]
    idx.sort(key=lambda r: r["symbol"] != "NIFTY")
    return {
        "indices": idx,
        "bullish": [card(r) for r in scanner.ranked("bullish", limit)],
        "bearish": [card(r) for r in scanner.ranked("bearish", limit)],
        "counts": {
            "bullish": sum(1 for r in res if r["bias"] == "bullish"),
            "bearish": sum(1 for r in res if r["bias"] == "bearish"),
            "choppy": sum(1 for r in res if r["bias"] == "choppy"),
        },
        "updated": scanner.updated,
    }


@app.get("/api/symbols")
async def symbols():
    idx = sorted(s for s, r in scanner.results.items() if r.get("kind") == "index")
    return idx + sorted(s for s, r in scanner.results.items() if r.get("kind") != "index")


TF_MINUTES = {"5": 5, "15": 15, "60": 60}


@app.get("/api/stock/{symbol}")
async def stock(symbol: str, sessions: int = 2, tf: str = "5"):
    symbol = symbol.upper()
    p = scanner.provider
    r = scanner.results.get(symbol)
    sd = p.stocks.get(symbol) if p else None
    if not r or not sd:
        raise HTTPException(404, f"No data for {symbol}")
    mins = TF_MINUTES.get(tf, 5)
    if mins == 60:
        sessions = max(sessions, 5)
    days = sorted({c.day for c in sd.candles})[-max(1, min(sessions, 10)):]
    keep = set(days)
    candles = aggregate([c for c in sd.candles if c.day in keep], mins)
    t0 = candles[0].t if candles else 0
    src = r["order_blocks"] if mins == 5 else r["mtf_obs"].get("15m" if mins == 15 else "1h", [])
    obs = [o for o in src if o["status"] != "broken" or o.get("broken_t", 0) >= t0]
    liq = [lv for lv in r["liquidity"] if lv["swept_idx"] is None]
    liq.sort(key=lambda lv: abs(lv["price"] - r["ltp"]))
    return {
        **card(r),
        "tf": str(mins),
        "atr": r["atr"], "adr_pct": r["adr_pct"], "structure": r["structure"],
        "prev_close": r["prev_close"],
        "plan": underlying_plan(r) if r["bias"] != "choppy" else None,
        "candles": [{"time": c.t, "open": c.o, "high": c.h, "low": c.l, "close": c.c, "volume": c.v}
                    for c in candles],
        "order_blocks": obs,
        "liquidity": liq[:8],
    }


@app.get("/api/options/{symbol}")
async def options(symbol: str, expiry: str = ""):
    symbol = symbol.upper()
    p = scanner.provider
    r = scanner.results.get(symbol)
    if not r or not p:
        raise HTTPException(404, f"No data for {symbol}")
    exps = p.expiries(symbol)
    if not exps:
        return {"expiries": [], "helper": None, "error": "No option expiries found for this symbol."}
    if expiry not in exps:
        expiry = exps[0]
    try:
        chain = await p.option_chain(symbol, expiry)
    except LoginRequired:
        raise HTTPException(401, "Upstox login needed")
    except Exception as e:
        return {"expiries": exps, "helper": None, "error": f"Option chain failed: {e}"}
    lot = p.stocks[symbol].lot_size or r.get("lot_size") or 1
    return {"expiries": exps, "helper": build_helper(r, chain, lot)}


@app.get("/api/xray")
async def xray():
    rows = [r for r in scanner.results.values() if r.get("kind") != "index"]
    points = [{"symbol": r["symbol"], "sector": r["sector"], "change_pct": r["change_pct"],
               "norm_move": r["norm_move"], "score": r["score"], "bias": r["bias"]} for r in rows]
    sectors: dict[str, dict] = {}
    for r in rows:
        s = sectors.setdefault(r["sector"], {"sector": r["sector"], "n": 0, "chg": 0.0, "signed": 0.0,
                                              "bullish": 0, "bearish": 0, "choppy": 0})
        s["n"] += 1
        s["chg"] += r["change_pct"]
        s["signed"] += r["score"] * (1 if r["bias"] == "bullish" else -1 if r["bias"] == "bearish" else 0)
        s[r["bias"]] += 1
    out = []
    for s in sectors.values():
        out.append({**s, "avg_change": round(s["chg"] / s["n"], 2), "strength": round(s["signed"] / s["n"], 1)})
    out.sort(key=lambda s: -s["avg_change"])
    adv = sum(1 for r in rows if r["change_pct"] > 0)
    return {
        "points": points,
        "sectors": out,
        "indices": [card(r) for r in scanner.results.values() if r.get("kind") == "index"],
        "breadth": {"advancing": adv, "declining": len(rows) - adv,
                    "bullish": sum(1 for r in rows if r["bias"] == "bullish"),
                    "bearish": sum(1 for r in rows if r["bias"] == "bearish"),
                    "choppy": sum(1 for r in rows if r["bias"] == "choppy")},
        "leading": out[0]["sector"] if out else None,
        "lagging": out[-1]["sector"] if out else None,
        "updated": scanner.updated,
    }


# --------------------------------------------------------------------- alerts

@app.get("/api/alerts")
async def get_alerts(since: int = 0):
    items = alerts.since(since)
    return {"items": items[-50:], "last_id": alerts.next_id - 1, "telegram_status": alerts.telegram_status}


@app.post("/api/alerts/test")
async def test_alert():
    item = alerts.add("TEST", "test", "Test alert from BlockRadar",
                      "If you can see/hear this, alerts are working.", 1, force=True)
    return {"ok": True, "id": item["id"] if item else None}


# --------------------------------------------------------------------- paper trading

@app.get("/api/paper")
async def paper_list():
    p = scanner.provider
    out = paper.summary()
    for r in out["rows"]:
        if r["status"] in ("OPEN", "PENDING") and p and r["symbol"] in p.stocks:
            r["u_ltp"] = p.stocks[r["symbol"]].ltp
    out["eod"] = load_settings().get("paper_eod", True)
    return out


@app.post("/api/paper")
async def paper_open(body: dict = Body(...)):
    p = scanner.provider
    sym = (body.get("symbol") or "").upper()
    if not p or sym not in p.stocks:
        raise HTTPException(400, "Unknown symbol")
    for k in ("direction", "u_entry", "u_stop", "u_target"):
        if body.get(k) in (None, ""):
            raise HTTPException(400, f"'{k}' is required")
    body["symbol"] = sym
    if body.get("instrument") == "OPTION":
        key = body.get("price_key")
        if not key:
            raise HTTPException(400, "Option key missing")
        price = p.option_price(key) or body.get("premium")
        body["side"] = "LONG"
        p.watch_keys = paper.watch_keys() | {key}
    else:
        body["instrument"] = "UNDERLYING"
        price = p.stocks[sym].ltp
    try:
        tid = paper.open(body, float(price) if price else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": tid}


@app.post("/api/paper/{tid}/close")
async def paper_close(tid: int):
    p = scanner.provider
    rows = paper.rows("WHERE id=?", (tid,))
    if not rows:
        raise HTTPException(404, "Not found")
    r = rows[0]
    price = None
    if p:
        price = p.stocks[r["symbol"]].ltp if r["instrument"] == "UNDERLYING" and r["symbol"] in p.stocks \
            else p.option_price(r["price_key"])
    return paper.close(tid, price, "MANUAL")


@app.delete("/api/paper/{tid}")
async def paper_delete(tid: int):
    paper.delete(tid)
    return {"ok": True}


# --------------------------------------------------------------------- settings & login

@app.get("/api/settings")
async def get_settings():
    s = load_settings()
    a = dict(s["alerts"])
    a["telegram_token"] = "saved" if a.get("telegram_token") else ""
    return {"mode": s["mode"], "picked": s.get("picked", False),
            "brokers": [public_info(k, s["brokers"].get(k, {})) for k in BROKERS],
            "alerts": a, "paper_eod": s.get("paper_eod", True)}


@app.post("/api/settings")
async def post_settings(body: dict = Body(...)):
    s = load_settings()
    if "picked" in body:
        s["picked"] = bool(body["picked"])
    if "paper_eod" in body:
        s["paper_eod"] = bool(body["paper_eod"])
    switch = False
    b = body.get("broker")
    if b in BROKERS and isinstance(body.get("creds"), dict):
        creds = s["brokers"].setdefault(b, {})
        allowed = {f["key"]: f for f in BROKERS[b]["fields"]}
        for k, v in body["creds"].items():
            if k not in allowed or v is None:
                continue
            v = str(v).strip()
            if allowed[k].get("secret") and not v:
                continue                     # blank secret = keep the saved one
            creds[k] = v
            if k == "token":                 # pasted token (Dhan): starts a new session
                creds["token_saved"] = now_ist().isoformat()
                if s["mode"] == b:
                    switch = True
    if isinstance(body.get("alerts"), dict):
        a = body["alerts"]
        for k in ("zone", "trap", "top", "paper"):
            if k in a:
                s["alerts"][k] = bool(a[k])
        for k in ("min_score", "cooldown_min"):
            if k in a:
                try:
                    s["alerts"][k] = max(0, int(a[k]))
                except (TypeError, ValueError):
                    pass
        if a.get("telegram_token") and a["telegram_token"] != "saved":
            s["alerts"]["telegram_token"] = str(a["telegram_token"]).strip()
        if a.get("telegram_token") == "":
            s["alerts"]["telegram_token"] = ""
        if "telegram_chat" in a:
            s["alerts"]["telegram_chat"] = str(a["telegram_chat"]).strip()
        alerts.configure(s["alerts"])
    mode = body.get("mode")
    if mode is not None and (mode == "demo" or (mode in BROKERS and BROKERS[mode]["status"] != "unsupported")):
        if mode != s["mode"]:
            s["mode"] = mode
            switch = True
        s["picked"] = True
    save_settings(s)
    if switch:
        await scanner.switch(make_provider(s))
    return await get_settings()


def _broker_creds(b: str) -> tuple[dict, dict]:
    s = load_settings()
    return s, s["brokers"].setdefault(b, {})


async def _activate(s: dict, b: str, token: str):
    creds = s["brokers"].setdefault(b, {})
    creds["token"] = token
    creds["token_saved"] = now_ist().isoformat()
    s["mode"] = b
    s["picked"] = True
    save_settings(s)
    await scanner.switch(make_provider(s))


def _back(msg: str):
    msg = html.escape(str(msg))
    return HTMLResponse(f"<body style='font-family:system-ui;background:#0a0d12;color:#e7eaf0;padding:40px'>"
                        f"<p>{msg}</p><p><a style='color:#f2b84b' href='/#settings'>Back to settings</a></p></body>",
                        status_code=400)


# Upstox keeps its original redirect URL (/auth/callback) so existing apps keep working
@app.get("/auth/login")
async def auth_login():
    return await broker_login("upstox")


@app.get("/auth/callback")
async def auth_callback(code: str = "", error: str = "", state: str = ""):
    if error or not code:
        return _back(f"Upstox login was cancelled or failed: {error or 'no code'}")
    if not _check_state("upstox", state):
        return _back("Login link expired or didn't start from this app. Click Connect again.")
    s, c = _broker_creds("upstox")
    try:
        token = await exchange_code(code, c.get("api_key", ""), c.get("api_secret", ""),
                                    c.get("redirect_uri") or "http://127.0.0.1:8000/auth/callback")
    except Exception as e:
        return _back(str(e))
    await _activate(s, "upstox", token)
    return RedirectResponse("/#board")


@app.get("/auth/{broker}/login")
async def broker_login(broker: str):
    if broker not in BROKERS or BROKERS[broker]["auth"] != "oauth":
        return RedirectResponse("/#settings")
    s, c = _broker_creds(broker)
    info = public_info(broker, c)
    if not info["configured"]:
        return RedirectResponse("/#settings")
    if broker == "upstox":
        return RedirectResponse(login_url(c["api_key"], c.get("redirect_uri") or "http://127.0.0.1:8000/auth/callback")
                                + "&" + urlencode({"state": _new_state("upstox")}))
    if broker == "fyers":
        return RedirectResponse(fyers_mod.login_url(c["app_id"], c.get("redirect_uri")
                                                    or "http://127.0.0.1:8000/auth/fyers/callback",
                                                    state=_new_state("fyers")))
    if broker == "zerodha":
        # Kite echoes "redirect_params" back on the redirect, which carries our state
        return RedirectResponse(zerodha_mod.login_url(c["api_key"]) + "&"
                                + urlencode({"redirect_params": urlencode({"state": _new_state("zerodha")})}))
    return RedirectResponse("/#settings")


@app.get("/auth/fyers/callback")
async def fyers_callback(auth_code: str = "", s: str = "", code: str = "", message: str = "", state: str = ""):
    if not auth_code:
        return _back(f"Fyers login was cancelled or failed: {message or s or code or 'no auth code'}")
    if not _check_state("fyers", state):
        return _back("Login link expired or didn't start from this app. Click Connect again.")
    st, c = _broker_creds("fyers")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            token = await fyers_mod.exchange_code(client, c.get("app_id", ""), c.get("secret", ""), auth_code)
    except Exception as e:
        return _back(str(e))
    await _activate(st, "fyers", token)
    return RedirectResponse("/#board")


@app.get("/auth/zerodha/callback")
async def zerodha_callback(request_token: str = "", status: str = "", state: str = ""):
    if not request_token or status not in ("", "success"):
        return _back(f"Zerodha login was cancelled or failed: {status or 'no request token'}")
    if not _check_state("zerodha", state):
        return _back("Login link expired or didn't start from this app. Click Connect again.")
    st, c = _broker_creds("zerodha")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            token = await zerodha_mod.exchange_code(client, c.get("api_key", ""), c.get("api_secret", ""), request_token)
    except Exception as e:
        return _back(str(e))
    await _activate(st, "zerodha", token)
    return RedirectResponse("/#board")


# --------------------------------------------------------------------- journal

@app.get("/api/journal")
async def journal_list():
    return {"trades": journal.list(), "stats": journal.stats()}


@app.post("/api/journal")
async def journal_add(body: dict = Body(...)):
    for k in ("symbol", "entry", "side"):
        if not body.get(k):
            raise HTTPException(400, f"'{k}' is required")
    body.setdefault("trade_date", now_ist().date().isoformat())
    try:
        return {"id": journal.add(body)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.put("/api/journal/{tid}")
async def journal_update(tid: int, body: dict = Body(...)):
    journal.update(tid, body)
    return {"ok": True}


@app.delete("/api/journal/{tid}")
async def journal_delete(tid: int):
    journal.delete(tid)
    return {"ok": True}
