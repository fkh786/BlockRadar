"""
Paper trading: simulated orders on the underlying or on an option, managed
automatically against live (or demo) prices. Stops, targets and limit entries
are triggered by the UNDERLYING price levels from the order-block plan, so an
option trade exits when the stock/index hits the level, at the option's
premium at that moment.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import now_ist

COLS = ("created", "symbol", "instrument", "label", "price_key", "direction", "side", "qty",
        "order_type", "u_entry", "u_stop", "u_target", "status", "fill_price", "fill_time",
        "exit_price", "exit_time", "exit_reason", "last_price", "note")


class PaperBook:
    def __init__(self, path: Path):
        self.path = path
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS paper (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created TEXT, symbol TEXT, instrument TEXT, label TEXT, price_key TEXT,
                    direction TEXT, side TEXT, qty REAL, order_type TEXT,
                    u_entry REAL, u_stop REAL, u_target REAL,
                    status TEXT, fill_price REAL, fill_time TEXT,
                    exit_price REAL, exit_time TEXT, exit_reason TEXT, last_price REAL, note TEXT
                )""")

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ queries
    @staticmethod
    def _pnl(row: dict):
        sign = 1 if row["side"] == "LONG" else -1
        if row["status"] == "CLOSED" and row["exit_price"] is not None:
            return round((row["exit_price"] - row["fill_price"]) * row["qty"] * sign, 2)
        if row["status"] == "OPEN" and row["last_price"] is not None:
            return round((row["last_price"] - row["fill_price"]) * row["qty"] * sign, 2)
        return None

    def rows(self, where: str = "", args=()) -> list[dict]:
        with self._conn() as c:
            out = [dict(r) for r in c.execute(f"SELECT * FROM paper {where} ORDER BY id DESC", args)]
        for r in out:
            r["pnl"] = self._pnl(r)
        return out

    def active(self) -> list[dict]:
        return self.rows("WHERE status IN ('PENDING','OPEN')")

    def watch_keys(self) -> set[str]:
        return {r["price_key"] for r in self.active() if r["instrument"] == "OPTION" and r["price_key"]}

    def summary(self) -> dict:
        rows = self.rows()
        closed = [r for r in rows if r["status"] == "CLOSED" and r["pnl"] is not None]
        wins = [r for r in closed if r["pnl"] > 0]
        open_ = [r for r in rows if r["status"] == "OPEN"]
        gross_w = sum(r["pnl"] for r in wins)
        gross_l = -sum(r["pnl"] for r in closed if r["pnl"] <= 0)
        curve, run = [], 0.0
        for r in sorted(closed, key=lambda r: r["id"]):
            run += r["pnl"]
            curve.append(round(run, 2))
        reasons: dict[str, int] = {}
        for r in closed:
            reasons[r["exit_reason"] or "?"] = reasons.get(r["exit_reason"] or "?", 0) + 1
        return {
            "rows": rows,
            "stats": {
                "closed": len(closed),
                "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
                "realised": round(sum(r["pnl"] for r in closed), 2),
                "unrealised": round(sum(r["pnl"] or 0 for r in open_), 2),
                "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
                "exits": reasons,
                "curve": curve,
            },
        }

    # ------------------------------------------------------------------ actions
    def open(self, d: dict, price_now: float | None) -> int:
        stamp = now_ist().strftime("%Y-%m-%d %H:%M")
        market = d.get("order_type", "MARKET") == "MARKET"
        row = {
            "created": stamp, "symbol": d["symbol"], "instrument": d.get("instrument", "UNDERLYING"),
            "label": d.get("label") or d["symbol"], "price_key": d.get("price_key") or "",
            "direction": d["direction"], "side": d.get("side") or ("LONG" if d["direction"] == "bull" else "SHORT"),
            "qty": float(d.get("qty") or 1), "order_type": "MARKET" if market else "LIMIT",
            "u_entry": float(d["u_entry"]), "u_stop": float(d["u_stop"]), "u_target": float(d["u_target"]),
            "status": "OPEN" if market else "PENDING",
            "fill_price": price_now if market else None, "fill_time": stamp if market else None,
            "exit_price": None, "exit_time": None, "exit_reason": None,
            "last_price": price_now, "note": d.get("note", ""),
        }
        if market and price_now is None:
            raise ValueError("No live price for this instrument yet - try again in a minute.")
        with self._conn() as c:
            cur = c.execute(f"INSERT INTO paper ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                            [row[k] for k in COLS])
            return cur.lastrowid

    def _set(self, tid: int, **kw):
        with self._conn() as c:
            c.execute(f"UPDATE paper SET {','.join(f'{k}=?' for k in kw)} WHERE id=?", [*kw.values(), tid])

    def close(self, tid: int, price: float | None, reason: str):
        rows = self.rows("WHERE id=?", (tid,))
        if not rows:
            return None
        r = rows[0]
        stamp = now_ist().strftime("%Y-%m-%d %H:%M")
        if r["status"] == "PENDING":
            self._set(tid, status="CANCELLED", exit_reason=reason, exit_time=stamp)
        elif r["status"] == "OPEN":
            px = price if price is not None else r["last_price"]
            self._set(tid, status="CLOSED", exit_price=px, exit_time=stamp, exit_reason=reason, last_price=px)
        return self.rows("WHERE id=?", (tid,))[0]

    def delete(self, tid: int):
        with self._conn() as c:
            c.execute("DELETE FROM paper WHERE id=? AND status IN ('CLOSED','CANCELLED')", (tid,))

    # ------------------------------------------------------------------ engine
    def update(self, u_price, opt_price, clock: str, market_open: bool, eod: bool = True) -> list[dict]:
        """
        u_price(symbol) -> underlying LTP; opt_price(key) -> option premium.
        Returns events: {"row", "event"} with event in FILLED / TARGET / STOP / EOD / CANCELLED.
        """
        events = []
        if not market_open:
            return events
        square_off = eod and clock >= "15:20"
        for r in self.active():
            u = u_price(r["symbol"])
            if u is None:
                continue
            px = u if r["instrument"] == "UNDERLYING" else opt_price(r["price_key"])
            bull = r["direction"] == "bull"
            stamp = now_ist().strftime("%Y-%m-%d %H:%M")
            if r["status"] == "PENDING":
                if square_off:
                    self._set(r["id"], status="CANCELLED", exit_reason="Not filled by close", exit_time=stamp)
                    events.append({"row": r, "event": "CANCELLED"})
                elif (bull and u <= r["u_stop"]) or (not bull and u >= r["u_stop"]):
                    self._set(r["id"], status="CANCELLED", exit_reason="Zone broke before fill", exit_time=stamp)
                    events.append({"row": r, "event": "CANCELLED"})
                elif px is not None and ((bull and u <= r["u_entry"]) or (not bull and u >= r["u_entry"])):
                    self._set(r["id"], status="OPEN", fill_price=px, fill_time=stamp, last_price=px)
                    r.update(status="OPEN", fill_price=px)
                    events.append({"row": r, "event": "FILLED"})
                continue
            if px is None:
                continue
            self._set(r["id"], last_price=px)
            reason = None
            if square_off:
                reason = "EOD"
            elif (bull and u <= r["u_stop"]) or (not bull and u >= r["u_stop"]):
                reason = "STOP"
            elif (bull and u >= r["u_target"]) or (not bull and u <= r["u_target"]):
                reason = "TARGET"
            if reason:
                self._set(r["id"], status="CLOSED", exit_price=px, exit_time=stamp, exit_reason=reason)
                r.update(status="CLOSED", exit_price=px, exit_reason=reason)
                r["pnl"] = self._pnl(r)
                events.append({"row": r, "event": reason})
        return events
