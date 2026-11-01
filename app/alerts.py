"""
Alerts: fired by the scanner after each analysis pass and by the paper-trading
book. The browser polls /api/alerts for sound + desktop notifications; Telegram
is optional and sent from here.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

import httpx

from .models import now_ist

DEFAULTS = {
    "min_score": 60,          # only stocks at least this strong (indices always count)
    "zone": True,             # price entered the trade zone
    "trap": True,             # fresh liquidity trap
    "top": False,             # stock entered the top 5 of its board
    "paper": True,            # paper-trading fills, stops, targets
    "cooldown_min": 20,       # same stock + same alert type at most once per N minutes
    "telegram_token": "",
    "telegram_chat": "",
}


class Alerts:
    def __init__(self):
        self.items: deque = deque(maxlen=300)
        self.next_id = 1
        self.prev: dict[str, dict] = {}
        self.prev_top: set[str] = set()
        self.last_sent: dict[tuple, float] = {}
        self.cfg = dict(DEFAULTS)
        self.telegram_status = ""

    def configure(self, cfg: dict):
        self.cfg = {**DEFAULTS, **(cfg or {})}

    def reset(self):
        self.prev.clear()
        self.prev_top.clear()

    # ------------------------------------------------------------------ add
    def add(self, symbol: str, kind: str, title: str, text: str, direction: int = 0, force: bool = False):
        key = (symbol, kind)
        now = time.time()
        if not force and now - self.last_sent.get(key, 0) < self.cfg["cooldown_min"] * 60:
            return None
        self.last_sent[key] = now
        item = {"id": self.next_id, "t": now, "time": now_ist().strftime("%H:%M:%S"), "symbol": symbol,
                "type": kind, "title": title, "text": text, "dir": direction}
        self.next_id += 1
        self.items.append(item)
        if self.cfg.get("telegram_token") and self.cfg.get("telegram_chat"):
            try:
                asyncio.get_running_loop().create_task(self._telegram(f"{title}\n{text}"))
            except RuntimeError:
                pass
        return item

    async def _telegram(self, msg: str):
        url = f"https://api.telegram.org/bot{self.cfg['telegram_token']}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(url, data={"chat_id": self.cfg["telegram_chat"], "text": msg})
            self.telegram_status = "ok" if r.status_code == 200 else f"Telegram error {r.status_code}: {r.text[:120]}"
        except Exception as e:
            self.telegram_status = f"Telegram error: {e}"

    def since(self, last_id: int) -> list[dict]:
        return [x for x in self.items if x["id"] > last_id]

    # ------------------------------------------------------------------ scanner hook
    def evaluate(self, results: dict, top: dict[str, list[str]]):
        cfg = self.cfg
        first_pass = not self.prev
        for sym, r in results.items():
            p = self.prev.get(sym)
            eligible = r.get("kind") == "index" or r["score"] >= cfg["min_score"]
            if p and eligible and not first_pass:
                arrow = 1 if r["bias"] == "bullish" else -1 if r["bias"] == "bearish" else 0
                if cfg["zone"] and r["in_zone"] and not p["in_zone"] and r["zone"]:
                    z = r["zone"]
                    extra = f" · {'+'.join(r['htf_confluence'])} zone confluence" if r.get("htf_confluence") else ""
                    self.add(sym, "zone", f"{sym} entered its {'bullish' if arrow > 0 else 'bearish'} order block",
                             f"Price {r['ltp']} inside {z['bottom']}–{z['top']} · strength {r['score']}{extra}", arrow)
                trap = r.get("trap")
                if cfg["trap"] and trap and (not p["trap_t"] or p["trap_t"] != trap["t"]):
                    self.add(sym, "trap", f"{sym}: liquidity trap ({'bullish' if trap['dir'] > 0 else 'bearish'})",
                             f"{trap['text']} · price {r['ltp']} · strength {r['score']}", trap["dir"])
            self.prev[sym] = {"in_zone": r["in_zone"], "trap_t": (r.get("trap") or {}).get("t")}

        now_top = set(top.get("bullish", [])[:5]) | set(top.get("bearish", [])[:5])
        if cfg["top"] and self.prev_top and not first_pass:
            for sym in now_top - self.prev_top:
                r = results[sym]
                self.add(sym, "top", f"{sym} is now top-5 {r['bias']}",
                         f"Strength {r['score']} · {r['change_pct']:+.2f}%", 1 if r["bias"] == "bullish" else -1)
        self.prev_top = now_top
